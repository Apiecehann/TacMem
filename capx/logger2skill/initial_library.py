from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from capx.tactile_skills.library import TactileSkillLibrary

INITIAL_METADATA = "initial_library.json"


class CalibrationProtectionError(ValueError):
    pass


def calibration_notice(root: Path) -> str:
    meta = initial_metadata(root)
    if not meta:
        return ""
    policy = meta.get("calibration_bindings")
    if not policy or policy.get("version") != 1:
        raise CalibrationProtectionError(
            "Initial library has no calibration bindings; start a new experiment from the frozen source")
    names = ", ".join(entry["name"] for entry in policy["entries"])
    return (
        "Frozen calibration entries are read-only: " + names + ". "
        "Preserve each complete entry, including its feature formula, channel/axis, units, "
        "measurement conditions, thresholds, applicability and original evidence. "
        "One successful or failed episode is not a recalibration. Do not transfer old "
        "calibration data to a new feature or override these entries in another skill. "
        "Automatic skill changes are limited to action strategies: grasp poses, approach paths, "
        "operation order, probing actions, recovery, placement and release, within the fixed "
        "measurement protocol, safety limits and task requirements. Do not add new perception "
        "calibrations, feature definitions, classification thresholds, force mappings or success "
        "criteria under any name or section. Keep proposed recalibrations outside the delivered "
        "library; tactile readings may guide actions using the existing fixed calibration."
    )


def _markdown_entries(text: str, family: str | None = None) -> dict[str, list[str]]:
    import yaml

    boundaries = list(re.finditer(r"(?m)^---[ \t]*\r?\n", text))
    starts = []
    for opening, closing in zip(boundaries, boundaries[1:]):
        header = text[opening.end():closing.start()]
        try:
            value = yaml.safe_load(header)
        except yaml.YAMLError:
            continue
        if (isinstance(value, dict) and isinstance(value.get("name"), str)
                and value.get("family") in {"sense", "act", "place", "protocol"}
                and isinstance(value.get("id"), str)):
            starts.append((opening.start(), value["name"], value["family"]))
    entries = {}
    for index, (start, name, entry_family) in enumerate(starts):
        stop = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        if family is None or family == entry_family:
            entries.setdefault(name, []).append(text[start:stop].strip())
    return entries


def validate_calibration_bindings(root: Path) -> None:
    meta = initial_metadata(root)
    if not meta:
        return
    calibration_notice(root)
    policy = meta["calibration_bindings"]
    if policy.get("source_content_sha256") != meta["source_content_sha256"]:
        raise CalibrationProtectionError("Calibration bindings have a different source")
    entries = policy.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CalibrationProtectionError("Missing frozen calibration entries")
    markdown = root / "skill.md"
    text = markdown.read_text(encoding="utf-8") if markdown.exists() else None
    sections = _markdown_entries(text) if text is not None else None
    allowed_names = {entry["name"] for entry in entries}
    sense_names = (set(_markdown_entries(text, family="sense")) if text is not None
                   else {path.stem for path in (root / "skills/sense").glob("*.md")})
    if extra := sense_names - allowed_names:
        raise CalibrationProtectionError(
            "Automatic updates are action strategies only; new perception calibration entries "
            "are not allowed: " + ", ".join(sorted(extra)))
    seen = set()
    for entry in entries:
        name, rel = entry["name"], Path(entry["path"])
        expected = entry["content"]
        if (name in seen or rel.as_posix() != f"skills/sense/{name}.md"
                or hashlib.sha256(expected.encode()).hexdigest() != entry["sha256"]):
            raise CalibrationProtectionError("Invalid frozen calibration binding")
        seen.add(name)
        if sections is not None:
            actual = sections.get(name, [])
            unchanged = len(actual) == 1 and (actual[0] == expected.strip()
                or actual[0].startswith(expected.rstrip() + "\n\n"))
        else:
            path = root / rel
            unchanged = path.is_file() and path.read_bytes() == expected.encode()
        if not unchanged:
            raise CalibrationProtectionError(
                f"Frozen calibration changed or missing: {name}; "
                "feature, units, conditions, thresholds and source evidence must stay bound. "
                "Keep the current version and submit any proposed recalibration separately.")



def content_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Initial library symlink is not allowed: {path}")
        if path.is_file() and path.relative_to(root).as_posix() != INITIAL_METADATA:
            digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def verify_source(root: Path) -> dict:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["files"] + manifest["supporting_files"]
    paths = [entry["path"] for entry in entries]
    if len(paths) != len(set(paths)) or len(manifest["files"]) != manifest["skill_count"]:
        raise ValueError("Invalid initial library manifest counts")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Initial library symlink is not allowed: {path}")
    if {p.relative_to(root).as_posix() for p in root.glob("skills/*/*.md")} != {
            entry["path"] for entry in manifest["files"]}:
        raise ValueError("Initial skill files differ from the manifest")
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item["path"]):
        rel = Path(entry["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Invalid source path in initial manifest")
        data = (root / rel).read_bytes()
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if (hashlib.sha256(data).hexdigest() != entry["sha256"]
                or len(data) != entry["size_bytes"] or blob != entry["git_blob_sha1"]):
            raise ValueError(f"Initial source checksum mismatch: {rel}")
        digest.update(rel.as_posix().encode() + b"\0" + data)
    if digest.hexdigest() != manifest["source_content_sha256"]:
        raise ValueError("Initial source aggregate checksum mismatch")
    library = TactileSkillLibrary.load(root)
    external = {ref for skill in library
                for ref in re.findall(r"\[\[([^\]]+)\]\]", skill.body + skill.summary)
                if ref not in library.skills}
    registered = {entry["name"] for entry in manifest["external_document_references"]
                  if entry["included"] is False}
    if external != registered:
        raise ValueError("Initial external references differ from their registration")
    return manifest


def initial_metadata(root: Path) -> dict | None:
    path = root / INITIAL_METADATA
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not value.get("source_content_sha256"):
        raise ValueError("Invalid initial library metadata")
    return value


def check_frozen_metadata(candidate: Path, parent: Path) -> None:
    before, after = parent / INITIAL_METADATA, candidate / INITIAL_METADATA
    if before.exists() != after.exists() or (before.exists() and before.read_bytes() != after.read_bytes()):
        raise CalibrationProtectionError("The curator changed the frozen initial library metadata")


def build_initial(source: Path, target: Path, memory_format: str, tasks: tuple[str, ...]) -> None:
    manifest = verify_source(source)
    library = TactileSkillLibrary.load(source)
    priority = []
    for task in tasks:
        if library.task_type_of(task) is None:
            raise ValueError(f"Unknown initial memory route: {task}")
        priority.extend(skill.name for skill in library.for_task(task))
    ordered = list(dict.fromkeys(priority + list(library.skills)))
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source / "taxonomy.yaml", target / "taxonomy.yaml")
    if memory_format == "skillopt_markdown":
        text = "\n\n".join(library.skills[name].source.read_text(encoding="utf-8")
                             for name in ordered)
        (target / "skill.md").write_text(text, encoding="utf-8")
    else:
        for entry in manifest["files"]:
            path = target / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / entry["path"], path)
    metadata = {
        "schema_version": 1,
        "source": manifest["source"],
        "manifest_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest(),
        "source_content_sha256": manifest["source_content_sha256"],
        "initial_content_sha256": content_digest(target),
        "known_document_names": list(library.skills),
        "external_document_references": manifest["external_document_references"],
        "blocked_tasks": ["arm_shadow_piano"] if manifest.get("runtime", {}).get(
            "excluded_from_current_digit_delivery") else [],
        "common_views": {task: [skill.render(compact=True) for skill in library.for_task(task)]
                         for task in tasks},
        "document_order": ordered,
        "calibration_bindings": {
            "version": 1,
            "source_content_sha256": manifest["source_content_sha256"],
            "entries": [
                {"name": skill.name, "path": skill.source.relative_to(source).as_posix(),
                 "sha256": hashlib.sha256(skill.source.read_bytes()).hexdigest(),
                 "content": skill.source.read_text(encoding="utf-8")}
                for skill in library if skill.family == "sense"
            ],
        },
    }
    (target / INITIAL_METADATA).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_source_identity(version: Path, source: Path) -> None:
    manifest = verify_source(source)
    meta = initial_metadata(version)
    if (not meta or meta["source_content_sha256"] != manifest["source_content_sha256"]
            or meta["manifest_sha256"] != hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()):
        raise ValueError("Existing active library has a different initial source; use a new experiment directory")
    validate_calibration_bindings(version)


def common_view(root: Path, task: str, max_chars: int) -> str | None:
    meta = initial_metadata(root)
    if not meta:
        return None
    if task in meta["blocked_tasks"]:
        return ""
    if (content_digest(root) != meta["initial_content_sha256"]
            or task not in meta["common_views"]):
        return None
    lines, size = [], 0
    for line in meta["common_views"][task]:
        if size + len(line) > max_chars:
            break
        lines.append(line)
        size += len(line)
    return "TACTILE SKILLS:\n" + "".join(lines) if lines else ""


def allowed_external_references(root: Path) -> set[str]:
    meta = initial_metadata(root)
    return {item["name"] for item in meta["external_document_references"]} if meta else set()


def check_markdown_references(root: Path, text: str) -> None:
    meta = initial_metadata(root)
    if not meta:
        return
    known = set(meta["known_document_names"]) | allowed_external_references(root)
    known.update(re.findall(r"^name:\s*[\'\" ]*([a-z0-9_-]+)", text, re.M))
    known.update(re.findall(r"^#+\s+([a-z0-9_-]+)(?:\s|$)", text, re.M))
    unknown = set(re.findall(r"\[\[([^\]]+)\]\]", text)) - known
    if unknown:
        raise ValueError(f"Unresolved skill reference: {sorted(unknown)}")
