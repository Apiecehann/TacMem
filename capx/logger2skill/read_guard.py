from __future__ import annotations

import hashlib
import json
import re
import shlex
import sys
from pathlib import Path


def check_read(event: dict, manifest: dict) -> dict:
    tool = event.get("tool_name")
    request = event.get("tool_input", {})
    name = request.get("file_path", "")
    if tool == "Bash":
        command = shlex.split(request.get("command", ""))
        if len(command) != 3 or command[:2] != ["rm", "--"] or not Path(command[2]).is_absolute():
            return {"tool": tool, "allowed": False, "input": request}
        name = command[2]
    root = Path(manifest["root"])
    raw = root / name
    path = raw.resolve()
    if ".." in raw.parts or any(p.is_symlink() for p in (raw, *raw.parents)):
        return {"tool": tool, "path": str(path), "allowed": False, "input": request}
    editable = manifest.get("write_root")
    mutable = False
    if editable and path.is_relative_to(editable):
        rel = path.relative_to(editable)
        mutable = (len(rel.parts) == 3 and rel.parts[0] == "skills"
                   and rel.parts[1] in {"sense", "act", "place", "protocol"}
                   and re.fullmatch(r"[a-z0-9][a-z0-9_-]*\.md", rel.name) is not None
                   and (not path.exists() or (path.is_file() and path.stat().st_nlink == 1)))
    expected = manifest["files"].get(str(path))
    allowed = (tool == "Read" and path.is_relative_to(root)
               and expected is not None and path.is_file())
    if allowed:
        allowed = hashlib.sha256(path.read_bytes()).hexdigest() == expected
    if mutable:
        allowed = tool in {"Edit", "Write"} or (tool in {"Read", "Bash"} and path.is_file())
    return {"tool": tool, "path": str(path), "allowed": allowed, "input": request}


def main(manifest_path: Path, audit_path: Path) -> int:
    try:
        event = json.load(sys.stdin)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = check_read(event, manifest)
    except Exception:
        record = {"allowed": False, "error": "Invalid public file request or manifest"}
    try:
        with audit_path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        print("Cannot record public file access", file=sys.stderr)
        return 2
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow" if record["allowed"] else "deny",
        "permissionDecisionReason": "Read frozen evidence or edit Markdown inside the isolated candidate only",
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
