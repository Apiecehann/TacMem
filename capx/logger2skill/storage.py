from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import yaml

from capx.tactile_skills.library import FAMILIES, ROOT, TactileSkillLibrary
from .initial_library import validate_calibration_bindings
from .initial_library import (INITIAL_METADATA, allowed_external_references, build_initial,
                              check_frozen_metadata, check_source_identity, common_view)


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


def validate_library(root: Path, taxonomy: bytes | None = None) -> TactileSkillLibrary:
    digest(root)
    validate_calibration_bindings(root)
    if taxonomy is not None and (root / "taxonomy.yaml").read_bytes() != taxonomy:
        raise ValueError("The curator changed the frozen task taxonomy")
    for path in root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(root)
            if rel.as_posix() not in {"taxonomy.yaml", INITIAL_METADATA} and not (
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
            if link not in library.skills and link not in allowed_external_references(root):
                raise ValueError(f"Unresolved skill reference: {link}")
    return library


def render_memory(root: Path, task: str, max_chars: int) -> str:
    library = validate_library(root)
    initial_view = common_view(root, task, max_chars)
    if initial_view is not None:
        return initial_view
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
    def __init__(self, root: Path):
        self.root = root
        self.active_file = root / "active.json"

    def initialize(self, initial_library_path: str | Path | None = None,
                   task_order: tuple[str, ...] = ()) -> dict:
        if self.active_file.exists():
            pointer = self.active()
            if initial_library_path:
                check_source_identity(self.path(pointer), Path(initial_library_path))
            return pointer
        if initial_library_path:
            initial = self.root / "versions" / "initial"
            build_initial(Path(initial_library_path), initial, "capx", tuple(task_order))
            validate_library(initial)
            pointer = {"version": "initial", "sha256": digest(initial)}
            write_json(self.active_file, pointer)
            return pointer
        taxonomy = TactileSkillLibrary._load_taxonomy(ROOT / "taxonomy.yaml")
        initial = self.root / "versions" / "empty"
        if not initial.exists():
            initial.mkdir(parents=True)
            for family in FAMILIES:
                (initial / "skills" / family).mkdir(parents=True)
            (initial / "taxonomy.yaml").write_text(
                yaml.safe_dump({"task_types": {k: list(v) for k, v in taxonomy.items()}},
                               sort_keys=False), encoding="utf-8")
        if len(validate_library(initial)):
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
        check_frozen_metadata(candidate, parent)
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
    return "\n\n".join(
        f"<!-- capx-file: {path.relative_to(root).as_posix()} -->\n"
        f"{path.read_text(encoding='utf-8').rstrip()}\n<!-- capx-end -->"
        for path in sorted((root / "skills").glob("*/*.md"))
    )


def unpack_library(document: str, target: Path) -> None:
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
