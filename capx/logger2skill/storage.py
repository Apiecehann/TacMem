from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import yaml

from capx.tactile_skills.library import FAMILIES, ROOT, TactileSkillLibrary


MARKDOWN_FILE = "skill.md"
MARKDOWN_FORMAT = "skillopt_markdown"


def library_format(root: Path) -> str:
    return MARKDOWN_FORMAT if (root / MARKDOWN_FILE).is_file() else "capx"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(root: Path) -> str:
    result = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Library symlink is not allowed: {path}")
        if path.is_file():
            result.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return result.hexdigest()


def validate_library(root: Path, taxonomy: bytes | None = None) -> TactileSkillLibrary | str:
    digest(root)
    if taxonomy is not None and (root / "taxonomy.yaml").read_bytes() != taxonomy:
        raise ValueError("The curator changed the frozen task taxonomy")
    if library_format(root) == MARKDOWN_FORMAT:
        TactileSkillLibrary._load_taxonomy(root / "taxonomy.yaml")
        for path in root.rglob("*"):
            if path.is_file() and path.relative_to(root).as_posix() not in {"taxonomy.yaml", MARKDOWN_FILE}:
                raise ValueError(f"Unexpected Markdown library file: {path.relative_to(root)}")
        return (root / MARKDOWN_FILE).read_text(encoding="utf-8")
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root)
            if rel.as_posix() != "taxonomy.yaml" and not (
                len(rel.parts) == 3 and rel.parts[0] == "skills"
                and rel.parts[1] in FAMILIES and rel.suffix == ".md"
            ):
                raise ValueError(f"Unexpected library file: {rel}")
    library = TactileSkillLibrary.load(root, allow_empty=True)
    ids = set()
    for skill in library:
        if not skill.id or skill.id in ids or not skill.summary or not skill.evidence:
            raise ValueError(f"Missing summary/evidence or duplicate id: {skill.name}")
        if skill.source.parent.name != skill.family:
            raise ValueError(f"Wrong family directory: {skill.name}")
        if not any(skill in library.for_task(t) for t in library._type_of_task):
            raise ValueError(f"Unreachable skill: {skill.name}")
        ids.add(skill.id)
        for link in re.findall(r"\[\[([^\]]+)\]\]", skill.body + skill.summary):
            if link not in library.skills:
                raise ValueError(f"Unresolved skill reference: {link}")
    return library


def render_memory(root: Path, task: str, max_chars: int) -> str:
    library = validate_library(root)
    if isinstance(library, str):
        taxonomy = TactileSkillLibrary._load_taxonomy(root / "taxonomy.yaml")
        if task not in TactileSkillLibrary._type_of_task_static(taxonomy):
            raise ValueError(f"Unknown memory route: {task}")
        text = library.strip()
        if max_chars <= 0:
            return ""
        if len(text) > max_chars:
            end = text.rfind("\n\n", 0, max_chars + 1)
            if end < 0:
                end = text.rfind("\n", 0, max_chars + 1)
            text = text[:end].rstrip() if end > 0 else ""
        return "TACTILE SKILLS:\n" + text if text else ""
    if library.task_type_of(task) is None:
        raise ValueError(f"Unknown memory route: {task}")
    lines = []
    size = 0
    for skill in library.for_task(task):
        line = skill.render(compact=True)
        if size + len(line) > max_chars:
            break
        lines.append(line)
        size += len(line)
    return "TACTILE SKILLS:\n" + "".join(lines) if lines else ""


class LibraryStore:
    def __init__(self, root: Path, memory_format: str = "capx"):
        if memory_format not in {"capx", MARKDOWN_FORMAT}:
            raise ValueError(f"Unknown memory format: {memory_format}")
        self.root = root
        self.active_file = root / "active.json"
        self.memory_format = memory_format

    def initialize(self) -> dict:
        if self.active_file.exists():
            pointer = self.active()
            if library_format(self.path(pointer)) != self.memory_format:
                raise ValueError("Existing library has a different format; use a separate experiment directory")
            return pointer
        taxonomy = TactileSkillLibrary._load_taxonomy(ROOT / "taxonomy.yaml")
        initial = self.root / "versions" / "empty"
        if not initial.exists():
            initial.mkdir(parents=True)
            if self.memory_format == MARKDOWN_FORMAT:
                (initial / MARKDOWN_FILE).write_text("", encoding="utf-8")
            else:
                for family in FAMILIES:
                    (initial / "skills" / family).mkdir(parents=True)
            (initial / "taxonomy.yaml").write_text(
                yaml.safe_dump({"task_types": {k: list(v) for k, v in taxonomy.items()}},
                               sort_keys=False), encoding="utf-8")
        if library_format(initial) != self.memory_format:
            raise ValueError("Cold-start directory has a different memory format")
        validate_library(initial)
        if pack_library(initial).strip():
            raise ValueError("Cold-start directory already contains learned skills")
        pointer = {"version": "empty", "sha256": digest(initial)}
        write_json(self.active_file, pointer)
        return pointer

    def path(self, pointer: dict) -> Path:
        version = pointer["version"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", version):
            raise ValueError("Invalid library version")
        path = self.root / "versions" / version
        if digest(path) != pointer["sha256"]:
            raise ValueError(f"Library version was modified: {version}")
        return path

    def active(self) -> dict:
        pointer = read_json(self.active_file)
        self.path(pointer)
        return pointer

    def adopt(self, candidate: Path, expected: dict, version: str) -> dict:
        if self.active() != expected:
            raise ValueError("Active library changed during this round")
        parent = self.path(expected)
        if library_format(candidate) != library_format(parent):
            raise ValueError("The curator changed the memory format")
        validate_library(candidate, (parent / "taxonomy.yaml").read_bytes())
        sha = digest(candidate)
        if sha == expected["sha256"]:
            return expected
        target = self.root / "versions" / version
        if target.exists():
            if digest(target) != sha:
                raise ValueError("Existing version has different content")
        else:
            shutil.copytree(candidate, target)
        pointer = {"version": version, "sha256": sha}
        write_json(self.active_file, pointer)
        return pointer


def pack_library(root: Path) -> str:
    if library_format(root) == MARKDOWN_FORMAT:
        return (root / MARKDOWN_FILE).read_text(encoding="utf-8")
    return "\n\n".join(
        f"<!-- capx-file: {path.relative_to(root).as_posix()} -->\n"
        f"{path.read_text(encoding='utf-8').rstrip()}\n<!-- capx-end -->"
        for path in sorted((root / "skills").glob("*/*.md"))
    )


def unpack_library(document: str, target: Path) -> None:
    if library_format(target) == MARKDOWN_FORMAT:
        (target / MARKDOWN_FILE).write_text(document, encoding="utf-8")
        return
    pattern = re.compile(r"<!-- capx-file: (skills/[^\n]+) -->\n(.*?)\n<!-- capx-end -->", re.S)
    matches = list(pattern.finditer(document))
    if pattern.sub("", document).strip():
        raise ValueError("SkillOpt produced text outside the file delimiters")
    files = {}
    for match in matches:
        rel, body = match.groups()
        path = Path(rel)
        if (len(path.parts) != 3 or path.parts[1] not in FAMILIES
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.md", path.name) or rel in files):
            raise ValueError(f"Invalid or duplicated SkillOpt file: {rel}")
        files[rel] = body + "\n"
    for path in (target / "skills").glob("*/*.md"):
        path.unlink()
    for rel, body in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
