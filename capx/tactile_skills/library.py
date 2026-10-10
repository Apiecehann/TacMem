"""Tactile skill library: reusable reading and acting recipes, routed by task.

Three things sit next to each other and say different things:

  * an API docstring says how to *call* a function;
  * a task PROMPT says what the task *wants*;
  * a skill says how to *find something out* -- which channel to read, what the
    reading means, what invalidates it, and which trap the obvious version
    falls into.

The third used to be paraphrased into every task PROMPT and every docstring
section separately, and the copies drifted: the same rule stated twice with
different thresholds, or with the numbers in one place and the caveat in the
other.  Here each skill is one markdown file under ``skills/<family>/``,
frontmatter naming the task types it applies to, so a task receives exactly
the skills its type routes to.

Not to be confused with ``capx.skills``, which extracts Python functions from
successful trial code -- this library holds prose, not code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

FAMILIES = ("sense", "act", "place", "protocol")
"""What a skill is for.

``sense``    read a physical quantity out of the gel
``act``      let a tactile signal start, stop or correct a motion
``place``    put an object down and confirm it stayed
``protocol`` sequence and design rules that span the other three

Everything here is delivered to a task, so everything here has to be worth a
line of that task's prompt.  Disproved ideas are not: they are for whoever
writes the next criterion, they would never be routed anywhere, and they live
in `docs/univtac_tactile_skill_inventory.md` §4.5 instead.
"""

ROOT = Path(__file__).resolve().parent
TAXONOMY_FILE = "taxonomy.yaml"

_REQUIRED_FIELDS = ("id", "name", "family", "summary", "task_types")
_OPTIONAL_FIELDS = ("tasks", "channels", "objects", "evidence", "copies")


class SkillError(ValueError):
    """A skill file or the taxonomy is malformed.  Raised at load time on
    purpose: a mistyped task name would otherwise route a skill to nobody."""


@dataclass(frozen=True)
class TactileSkill:
    """One reading or acting recipe.

    Attributes:
        id: Stable short id (``S1``, ``A10``) used to cross-reference the
            inventory document.
        name: Slug; also the file stem and the routing handle.
        family: One of ``FAMILIES``.
        summary: One line, the thing to remember if nothing else is read.
        task_types: Task types this skill is routed to.
        tasks: Individual task names, on top of ``task_types``.
        channels: Tactile channels the skill reads (``shear``, ``anisotropy``,
            ``depth``, ``force``, ``coincidence``...).
        objects: Which objects the numbers hold for.
        evidence: Why the skill is believed, with the measurement behind it.
        copies: Where this rule is still written a second time outside the
            library -- today only in prose documents read by people or by the
            ViTaForge agent.  The copies in the task PROMPTs and in the API
            docstrings were deleted as the library took over, so a prompt no
            longer carries any of these.  Bookkeeping for the migration, never
            delivered to an LLM.
        body: Everything below the frontmatter.
        source: The file it was loaded from.
    """

    id: str
    name: str
    family: str
    summary: str
    task_types: tuple[str, ...]
    tasks: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    objects: str = ""
    evidence: str = ""
    copies: tuple[str, ...] = ()
    body: str = ""
    source: Path | None = None

    def render(self, *, provenance: bool = False, compact: bool = False) -> str:
        """Markdown for one skill.

        Args:
            provenance: Append evidence, per-object scope and where the skill
                is written today.  For humans; the prompt gets the body alone.
            compact: One line of rule, no prose.  The body is for a reader who
                came for this one skill; a prompt carrying dozens of them gets
                the rule alone.  The internal id stays out — it is bookkeeping
                for this library, and the prompt cannot look it up.
        """
        if compact:
            line = f"- {self.name}: {self.summary}"
            if provenance and self.copies:
                line += " (written today in: " + "; ".join(self.copies) + ")"
            return line + "\n"
        parts = [f"### {self.name} [{self.id}]", self.summary, "", self.body]
        if provenance:
            bits = []
            if self.evidence:
                bits.append(f"Evidence: {self.evidence}")
            if self.objects:
                bits.append(f"Numbers hold for: {self.objects}")
            if self.copies:
                bits.append("Written today in: " + "; ".join(self.copies))
            parts += ["", *bits]
        return "\n".join(parts).strip() + "\n"


class TactileSkillLibrary:
    """The loaded library: skills indexed by name, taxonomies by task.

    Args:
        skills: Slug -> skill, in file order.
        task_types: Task type -> task names it contains.
        root: Directory the library was loaded from.
    """

    def __init__(
        self,
        skills: dict[str, TactileSkill],
        task_types: dict[str, tuple[str, ...]],
        root: Path,
    ) -> None:
        self.skills = skills
        self.task_types = task_types
        self.root = root
        self._type_of_task = {
            task: task_type
            for task_type, tasks in task_types.items()
            for task in tasks
        }

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, root: Path | str | None = None) -> "TactileSkillLibrary":
        """Read every ``skills/*/*.md`` under ``root`` plus its taxonomy.

        Raises:
            SkillError: A file is malformed or names something unknown.
        """
        root = Path(root) if root is not None else ROOT
        task_types = cls._load_taxonomy(root / TAXONOMY_FILE)
        known_types = set(task_types)
        known_tasks = set(cls._type_of_task_static(task_types))

        skills: dict[str, TactileSkill] = {}
        for path in sorted((root / "skills").glob("*/*.md")):
            skill = cls._load_skill(path, known_types, known_tasks)
            if skill.name in skills:
                raise SkillError(f"{path}: duplicate skill name {skill.name!r}")
            skills[skill.name] = skill
        if not skills:
            raise SkillError(f"{root}: no skills found under skills/*/*.md")
        return cls(skills, task_types, root)

    @staticmethod
    def _type_of_task_static(task_types: dict[str, tuple[str, ...]]) -> dict[str, str]:
        return {t: ty for ty, ts in task_types.items() for t in ts}

    @staticmethod
    def _load_taxonomy(path: Path) -> dict[str, tuple[str, ...]]:
        if not path.exists():
            raise SkillError(f"{path}: missing taxonomy")
        data = yaml.safe_load(path.read_text()) or {}
        table = data.get("task_types")
        if not isinstance(table, dict) or not table:
            raise SkillError(f"{path}: expected a non-empty `task_types` mapping")
        out: dict[str, tuple[str, ...]] = {}
        seen: dict[str, str] = {}
        for task_type, tasks in table.items():
            if not isinstance(tasks, list) or not tasks:
                raise SkillError(f"{path}: task type {task_type!r} has no tasks")
            for task in tasks:
                if task in seen:
                    raise SkillError(
                        f"{path}: task {task!r} is in two types "
                        f"({seen[task]!r} and {task_type!r})"
                    )
                seen[task] = task_type
            out[str(task_type)] = tuple(str(t) for t in tasks)
        return out

    @staticmethod
    def _load_skill(
        path: Path, known_types: set[str], known_tasks: set[str]
    ) -> TactileSkill:
        meta, body = _split_frontmatter(path.read_text(), path)
        missing = [f for f in _REQUIRED_FIELDS if f not in meta]
        if missing:
            raise SkillError(f"{path}: frontmatter is missing {missing}")
        unknown = set(meta) - set(_REQUIRED_FIELDS) - set(_OPTIONAL_FIELDS)
        if unknown:
            raise SkillError(f"{path}: unknown frontmatter keys {sorted(unknown)}")
        if path.stem != str(meta["name"]):
            raise SkillError(f"{path}: name {meta['name']!r} != file stem")
        if meta["family"] not in FAMILIES:
            raise SkillError(f"{path}: family {meta['family']!r} not in {FAMILIES}")

        def _tuple(key: str) -> tuple[str, ...]:
            value = meta.get(key) or ()
            if isinstance(value, str):
                value = [value]
            return tuple(str(v) for v in value)

        task_types = _tuple("task_types")
        tasks = _tuple("tasks")
        if not (task_types or tasks):
            raise SkillError(f"{path}: names no task type and no task")
        for task_type in task_types:
            if task_type not in known_types:
                raise SkillError(f"{path}: unknown task type {task_type!r}")
        for task in tasks:
            if task not in known_tasks:
                raise SkillError(f"{path}: unknown task {task!r}")
        return TactileSkill(
            id=str(meta["id"]),
            name=str(meta["name"]),
            family=str(meta["family"]),
            summary=" ".join(str(meta["summary"]).split()),
            task_types=task_types,
            tasks=tasks,
            channels=_tuple("channels"),
            objects=str(meta.get("objects") or "").strip(),
            evidence=" ".join(str(meta.get("evidence") or "").split()),
            copies=_tuple("copies"),
            body=body,
            source=path,
        )

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.skills)

    def __iter__(self):
        return iter(self.skills.values())

    def __contains__(self, name: object) -> bool:
        return name in self.skills

    def get(self, name: str) -> TactileSkill:
        """One skill by slug.

        Raises:
            KeyError: No such skill; the message lists the known ones.
        """
        try:
            return self.skills[name]
        except KeyError:
            raise KeyError(f"unknown skill {name!r}; known: {sorted(self.skills)}") from None

    def family(self, family: str) -> tuple[TactileSkill, ...]:
        """Every skill in one family, in file order."""
        return tuple(s for s in self if s.family == family)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def task_type_of(self, task: str) -> str | None:
        """The type a task belongs to, or None if the taxonomy does not know it."""
        return self._type_of_task.get(task)

    def for_task_type(self, task_type: str) -> tuple[TactileSkill, ...]:
        """Skills routed by type alone.

        Raises:
            KeyError: No such task type.
        """
        if task_type not in self.task_types:
            raise KeyError(
                f"unknown task type {task_type!r}; known: {sorted(self.task_types)}"
            )
        return tuple(s for s in self if task_type in s.task_types)

    def for_task(self, task: str) -> tuple[TactileSkill, ...]:
        """Skills a task receives: its type's, plus the ones naming it.

        Raises:
            KeyError: The taxonomy does not know the task.
        """
        task_type = self.task_type_of(task)
        if task_type is None:
            raise KeyError(
                f"unknown task {task!r}; known: {sorted(self._type_of_task)}"
            )
        return tuple(
            s for s in self if task_type in s.task_types or task in s.tasks
        )

    def render_for_task(
        self,
        task: str,
        *,
        compact: bool = True,
        provenance: bool = False,
    ) -> str:
        """The block a task gets, or "" when nothing routes to it.

        Args:
            task: Task name, as in ``taxonomy.yaml``.
            compact: One line per skill (the default — a prompt carrying a
                dozen skills is answering questions the task has not asked
                yet).  ``False`` renders each skill's whole body.
            provenance: Append evidence and where each skill is written today.
        """
        skills = self.for_task(task)
        if not skills:
            return ""
        task_type = self.task_type_of(task)
        head = f"TACTILE SKILLS (routed to {task}, type {task_type}):"
        if compact:
            # One line each, no blank line between them: this is a list, and
            # the prompt it lands in is not paying for whitespace.
            body = "\n".join(s.render(provenance=provenance, compact=True).rstrip("\n") for s in skills)
        else:
            body = "\n".join(s.render(provenance=provenance) for s in skills)
        return f"{head}\n{body}".strip()


def _split_frontmatter(text: str, path: Path) -> tuple[dict, str]:
    """``---`` YAML ``---`` then the body, which starts on the next line."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{path}: file must open with a --- frontmatter line")
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), None)
    if end is None:
        raise SkillError(f"{path}: frontmatter is never closed")
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        # An unquoted value containing ": " is the usual cause; say which file.
        raise SkillError(f"{path}: frontmatter is not valid YAML — {exc}") from None
    if not isinstance(meta, dict):
        raise SkillError(f"{path}: frontmatter is not a mapping")
    return meta, "\n".join(lines[end + 1 :]).strip("\n")


@lru_cache(maxsize=1)
def default_library() -> TactileSkillLibrary:
    """The library shipped with capx, loaded once."""
    return TactileSkillLibrary.load()
