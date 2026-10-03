import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import numpy as np
from PIL import Image

from capx.envs.base import BaseEnv

# A docstring may wrap optional paragraphs in `[section:tag]` / `[/section]`.
# Such a block is delivered only to APIs whose `doc_sections` contains `tag`,
# which lets one shared API class give each task just the reading recipes it
# needs.  Unmarked text is always delivered, and the markers are always
# stripped, so they never reach a prompt.
_SECTION_OPEN = "[section:"
_SECTION_CLOSE = "[/section]"


def _apply_doc_sections(doc: str, allowed: frozenset[str] | None) -> str:
    """Drop marked sections, keeping only the tags in allowed.

    Args:
        doc: A dedented docstring, optionally containing section markers.
        allowed: Section tags to keep.  None keeps every section, i.e. the
            whole docstring with the markers stripped.

    Returns:
        The docstring with unselected sections and all markers removed.
    """
    kept: list[str] = []
    keeping = True
    dropped = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.startswith(_SECTION_OPEN) and stripped.endswith("]"):
            tag = stripped[len(_SECTION_OPEN) : -1]
            keeping = allowed is None or tag in allowed
            dropped = dropped or not keeping
            continue
        if stripped == _SECTION_CLOSE:
            keeping = True
            continue
        if not keeping:
            dropped = True
            continue
        kept.append(line)
    text = "\n".join(kept) + ("\n" if doc.endswith("\n") else "")
    if not dropped:
        # Nothing was removed, so leave the text byte-for-byte alone: a full
        # profile has to reproduce the docstring exactly.
        return text
    # Collapse the blank runs the removed sections left behind -- including a
    # trailing one, when the section that went was the last thing in the doc.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip("\n") + ("\n" if doc.endswith("\n") else "")


class ApiBase(ABC):
    """Base class for tool APIs.

    Guidelines:
    - Expose public functions via `functions()`.
    - Each function should have a Google-style docstring with Args/Returns.
    - `combined_doc()` returns a standardized aggregate doc for prompts.
    - If your API needs access to the environment, implement `set_env(env)`.

    Example:
    class GraspPlanApi(ApiBase):
        def functions(self) -> dict[str, Callable[..., Any]]:
            # optionally, we can expose only one of the functions if needed
            return {"grasp_plan": self.grasp_plan, "grasp_plan_sim": self.grasp_plan_sim}

        def grasp_plan(self, depth: np.ndarray, intrinsics: np.ndarray) -> list[dict]:
            \"""
            Plan parallel-jaw grasps from a depth map.

            Args:
            depth: (H, W) float32 meters.
            intrinsics: (3, 3) float32 pinhole intrinsics.

            Returns:
            List of dicts: {pose: (4,4) float32, width: float, score: float}.
            \"""
            ...

        def grasp_plan_sim(self, depth: np.ndarray, intrinsics: np.ndarray) -> list[dict]:
            ...
    """

    # Function names that stay callable in executed code but are excluded
    # from the LLM prompt.  Used for ground-truth oracles (e.g. a final
    # success check) that the framework wants to invoke without the agent
    # knowing about them.
    _prompt_hidden: set[str] = set()

    # Optional API-doc sections this instance delivers: a subset of the
    # `[section:...]` tags its docstrings use.  None (the default) delivers
    # every section, i.e. opt-in per task -- see CodeExecutionEnvBase, which
    # sets this from the task's `api_doc_sections` class attribute.
    doc_sections: frozenset[str] | None = None

    def __init__(self, env: BaseEnv) -> None:
        self._env = env
        self._webui_enabled: bool = False

    def _doc_text(self, fn: Callable[..., Any]) -> str:
        """Docstring of fn, restricted to this instance's doc_sections."""
        return _apply_doc_sections(inspect.getdoc(fn) or "", self.doc_sections)

    def enable_webui(self, enabled: bool = True) -> None:
        """Enable or disable web UI execution logging for this API instance."""
        self._webui_enabled = enabled

    def _log_step(
        self,
        tool_name: str,
        text: str,
        images: list[np.ndarray | Image.Image | str]
        | np.ndarray
        | Image.Image
        | str
        | None = None,
        highlight: bool = False,
    ) -> None:
        """Log an execution step if web UI mode is enabled.

        This is a thin wrapper around :func:`capx.utils.execution_logger.log_step`
        that no-ops when the web UI flag is off, keeping control API code free of
        conditional checks.
        """
        if not self._webui_enabled:
            return
        from capx.utils.execution_logger import log_step

        log_step(tool_name=tool_name, text=text, images=images, highlight=highlight)

    def _log_step_update(
        self,
        text: str | None = None,
        images: list[np.ndarray | Image.Image | str]
        | np.ndarray
        | Image.Image
        | str
        | None = None,
    ) -> None:
        """Update the last logged step if web UI mode is enabled."""
        if not self._webui_enabled:
            return
        from capx.utils.execution_logger import log_step_update

        log_step_update(text=text, images=images)

    @abstractmethod
    def functions(self) -> dict[str, Callable[..., Any]]:
        """Return mapping of public function name -> callable."""

    def combined_doc(self) -> str:
        """Aggregate function docs in a simple, consistent format.

        Format per function:
            name(signature)
              Summary: first line of function doc
              Doc: full function docstring (Google style recommended)
        """
        # we need to discuss this design further down the line
        lines: list[str] = []
        # lines: list[str] = [f"API: {self.__class__.__name__}", ""]
        for name, fn in self.functions().items():
            if name in self._prompt_hidden:
                continue
            try:
                sig = str(inspect.signature(fn))
            except Exception:
                sig = "(…)"
            doc = self._doc_text(fn)
            # first = doc.splitlines()[0] if doc else ""
            lines.append(f"{name}{sig}")
            # if first:
            #     lines.append(f"  Summary: {first}")
            if doc:
                lines.append("  Doc:")
                lines.extend(f"    {ln}" for ln in doc.splitlines())
            lines.append("")
        return "\n".join(lines).strip()


_API_FACTORIES: dict[str, Callable[[], ApiBase]] = {}


def register_api(name: str, factory: Callable[[], ApiBase]) -> None:
    _API_FACTORIES[name] = factory


@lru_cache(maxsize=256)
def get_api(name: str) -> Callable[[BaseEnv], ApiBase]:
    if name not in _API_FACTORIES:
        raise KeyError(f"API '{name}' not registered")
    return _API_FACTORIES[name]


def list_apis() -> list[str]:
    return list(_API_FACTORIES.keys())
