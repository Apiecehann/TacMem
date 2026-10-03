"""Tests for per-task API-doc pruning (`[section:tag]` markers).

All 34 UniVTAC configs share one `UniVTACTactileApi`, so its docstrings are a
union of every reading recipe any task might need and there is nowhere to fork
them per task.  The markers let a docstring carry optional paragraphs that only
some tasks receive: `CodeExecutionEnvBase` reads the task class's
`api_doc_sections` and sets `ApiBase.doc_sections` on each API it builds.

No GPU, no simulator, no server: the APIs are instantiated without an env and
only their docstrings are read.  Four things are covered:

  1. The filter itself (`_apply_doc_sections`): unmarked text always survives,
     markers never do, and a profile that keeps every tag reproduces the
     docstring byte for byte.
  2. The delivered prompt for every subset of tags: no marker leaks, and a
     subset's output is a subsequence of a superset's.
  3. Tag hygiene — the tags present in the docstrings, the ones declared in
     `DOC_SECTION_TAGS`, and the ones the task classes ask for must agree.  A
     typo'd tag is otherwise silent: the section simply vanishes for every
     profile at once.
  4. Backward compatibility: `doc_sections` defaults to None, which delivers
     the whole docstring with the markers stripped, so an API with no markers
     (and any subclass overriding `combined_doc`) is unaffected.

A new task class must declare a profile: the default is "send everything", and
`test_every_task_class_declares_a_valid_profile` pins the current set of task
classes so adding one fails here until that choice is made.
"""

from __future__ import annotations

import importlib
import inspect
import itertools
import pkgutil

import pytest

from capx.envs.tasks import tactile as tactile_tasks
from capx.integrations.base_api import (
    _SECTION_CLOSE,
    _SECTION_OPEN,
    ApiBase,
    _apply_doc_sections,
)
from capx.integrations.tactile.univtac_api import (
    DOC_SECTION_TAGS,
    UniVTACTactileApi,
)

# Every subset of the tags, as the profile a task could declare.
PROFILES = [
    frozenset(c)
    for r in range(len(DOC_SECTION_TAGS) + 1)
    for c in itertools.combinations(DOC_SECTION_TAGS, r)
]


def _strip_markers(doc: str) -> str:
    """Drop marker lines only.  Deliberately not `_apply_doc_sections`, so the
    byte-exactness assertions below are a real check and not a tautology."""
    keep = [
        ln
        for ln in doc.splitlines()
        if not ln.strip().startswith(_SECTION_OPEN) and ln.strip() != _SECTION_CLOSE
    ]
    return "\n".join(keep) + ("\n" if doc.endswith("\n") else "")


def _api(doc_sections: frozenset[str] | None) -> UniVTACTactileApi:
    """A tactile API with no env and no server — docstrings only."""
    api = object.__new__(UniVTACTactileApi)
    api._env = None
    api._webui_enabled = False
    api._slip_baseline = None
    api.doc_sections = doc_sections
    return api


def _is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(line in it for line in small)


# --------------------------------------------------------------------------
# 1. The filter
# --------------------------------------------------------------------------


def test_unmarked_docstring_is_untouched() -> None:
    doc = "Summary.\n\nA paragraph.\n\n  Indented list:\n    item\n"
    assert _apply_doc_sections(doc, None) == doc
    assert _apply_doc_sections(doc, frozenset()) == doc


def test_markers_are_stripped_when_everything_is_kept() -> None:
    doc = "Keep me.\n\n[section:a]\nOptional.\n[/section]\n\nLast.\n"
    assert _apply_doc_sections(doc, frozenset({"a"})) == "Keep me.\n\nOptional.\n\nLast.\n"
    # None == "keep every section", same bytes.
    assert _apply_doc_sections(doc, None) == "Keep me.\n\nOptional.\n\nLast.\n"


def test_dropped_section_leaves_one_blank_line() -> None:
    doc = "First.\n\n[section:a]\nA only.\n[/section]\n\n[section:b]\nB only.\n[/section]\n\nLast.\n"
    out = _apply_doc_sections(doc, frozenset({"b"}))
    assert out == "First.\n\nB only.\n\nLast.\n"


def test_dropping_the_last_paragraph_leaves_no_trailing_blanks() -> None:
    doc = "First.\n\n[section:a]\nA only.\n[/section]\n"
    out = _apply_doc_sections(doc, frozenset())
    assert out == "First.\n"


def test_default_profile_strips_markers_only() -> None:
    """The point of the whole exercise: a task that wants everything it reads
    today must get the identical string, so adding markers to a docstring
    cannot change any existing prompt."""
    api = _api(None)
    for name, fn in api.functions().items():
        doc = inspect.getdoc(fn) or ""
        assert api._doc_text(fn) == _strip_markers(doc), name


def test_marker_syntax_needs_its_own_line() -> None:
    """A marker mid-paragraph would swallow the rest of the line's paragraph,
    so the filter only treats a whole stripped line as a marker."""
    doc = "Text mentioning [section:a] inline.\n"
    assert _apply_doc_sections(doc, frozenset()) == doc


# --------------------------------------------------------------------------
# 2. The delivered prompt, over every profile
# --------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: "+".join(sorted(p)) or "none")
def test_no_marker_reaches_the_prompt(profile: frozenset[str]) -> None:
    doc = _api(profile).combined_doc()
    assert _SECTION_OPEN not in doc
    assert _SECTION_CLOSE not in doc


def test_smaller_profile_is_a_subsequence_of_a_bigger_one() -> None:
    rendered = {p: _api(p).combined_doc().splitlines() for p in PROFILES}
    for a in PROFILES:
        for b in PROFILES:
            if a < b:
                assert _is_subsequence(rendered[a], rendered[b]), (sorted(a), sorted(b))


def test_full_profile_matches_the_unmarked_docstrings() -> None:
    """`combined_doc()` is a fixed framing around the per-function docstrings;
    with every tag kept, it has to equal the same framing built from the
    marker-stripped docstrings."""
    api = _api(frozenset(DOC_SECTION_TAGS))
    lines: list[str] = []
    for name, fn in api.functions().items():
        if name in api._prompt_hidden:
            continue
        doc = _strip_markers(inspect.getdoc(fn) or "")
        lines.append(f"{name}{inspect.signature(fn)}")
        if doc:
            lines.append("  Doc:")
            lines.extend(f"    {ln}" for ln in doc.splitlines())
        lines.append("")
    assert api.combined_doc() == "\n".join(lines).strip()


def test_a_plain_task_sees_a_strictly_shorter_doc() -> None:
    """Sanity on the payoff: a task that opts out of everything reads less."""
    full = len(_api(frozenset(DOC_SECTION_TAGS)).combined_doc().split())
    none = len(_api(frozenset()).combined_doc().split())
    assert none < full * 0.6


# --------------------------------------------------------------------------
# 3. Tag hygiene — the silent-failure guard
# --------------------------------------------------------------------------


def _tags_in_docstrings() -> set[str]:
    api = _api(None)
    found: set[str] = set()
    for fn in api.functions().values():
        doc = inspect.getdoc(fn) or ""
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped.startswith(_SECTION_OPEN) and stripped.endswith("]"):
                found.add(stripped[len(_SECTION_OPEN) : -1])
    return found


def test_every_tag_in_a_docstring_is_declared() -> None:
    assert _tags_in_docstrings() == set(DOC_SECTION_TAGS)


def test_unbalanced_markers_are_caught_by_the_equality_check() -> None:
    """A tag that appears but is never declared — or vice versa — is what the
    previous test exists to catch; pin the failure mode here."""
    assert _tags_in_docstrings() != set(DOC_SECTION_TAGS) | {"typo"}


def _task_classes() -> dict[str, type]:
    classes: dict[str, type] = {}
    for mod in pkgutil.iter_modules(tactile_tasks.__path__):
        if not mod.name.startswith("univtac_"):
            continue
        module = importlib.import_module(f"{tactile_tasks.__name__}.{mod.name}")
        for name, obj in vars(module).items():
            if not isinstance(obj, type) or obj.__module__ != module.__name__:
                continue
            if hasattr(obj, "oracle_code"):
                classes[name] = obj
    return classes


def test_every_task_class_declares_a_valid_profile() -> None:
    classes = _task_classes()
    assert len(classes) == 13, sorted(classes)
    for name, cls in classes.items():
        profile = getattr(cls, "api_doc_sections", None)
        assert profile is not None, f"{name} does not declare api_doc_sections"
        assert set(profile) <= set(DOC_SECTION_TAGS), f"{name}: {sorted(set(profile) - set(DOC_SECTION_TAGS))}"


def test_reading_recipes_go_to_the_tasks_that_read_them() -> None:
    classes = _task_classes()
    wants = {
        "UniVTACWeightClassifyCodeEnv": {"weight", "servo"},
        "UniVTACHardnessClassifyCodeEnv": {"hardness", "position"},
        "UniVTACGraspClassifyCodeEnv": {"texture", "servo"},
        "UniVTACTactileMemoryMatchCodeEnv": {
            "position",
            "hardness",
            "texture",
            "memory_match",
        },
        "UniVTACBulbTighteningCodeEnv": {"screw"},
    }
    for name, tags in wants.items():
        assert set(classes[name].api_doc_sections) == tags, name
    # The memory-match task deliberately omits the standalone force-threshold
    # weight recipe: its task-specific section integrates all axes into one
    # progressive position-commanded grasp.
    memory_doc = _api(
        frozenset(classes["UniVTACTactileMemoryMatchCodeEnv"].api_doc_sections)
    ).combined_doc()
    assert "TACTILE MEMORY MATCH uses one integrated measurement grasp" in memory_doc
    assert "Against a reference: close at force_threshold=27.7" not in memory_doc
    assert "In a valid baseline the two pads read within ~0.4 px" not in memory_doc
    assert "The servo does not land exactly on the requested depth" not in memory_doc
    # ...and the plain manipulation tasks read none of the optional recipes.
    plain = [n for n in classes if n not in wants]
    assert plain and all(classes[n].api_doc_sections == () for n in plain), plain


# --------------------------------------------------------------------------
# 4. Backward compatibility
# --------------------------------------------------------------------------


def test_opt_in_default_is_none() -> None:
    """Doing nothing must deliver everything — the mechanism is opt-in, so no
    existing API or task changes behaviour without asking for a profile."""
    assert ApiBase.doc_sections is None
    assert UniVTACTactileApi.doc_sections is None


def test_bare_api_base_delivers_the_full_docstring() -> None:
    class _Api(ApiBase):
        def functions(self):
            return {"fn": self.fn}

        def fn(self) -> None:
            """Summary.

            [section:a]
            Optional.
            [/section]
            """

    doc = _Api(None).combined_doc()
    assert "Optional." in doc
    assert _SECTION_OPEN not in doc and _SECTION_CLOSE not in doc


def test_a_subclass_overriding_combined_doc_is_unaffected() -> None:
    """`combined_doc(self)` has three overrides in the repo and takes no
    arguments; the profile rides on the instance, not the call."""

    class _Api(ApiBase):
        def functions(self):
            return {}

        def combined_doc(self) -> str:
            return "custom"

    assert _Api(None).combined_doc() == "custom"
