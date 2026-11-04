from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from .claude import ClaudeSession, summarize_calls
from .curators import curate_cc, curate_skillopt
from .storage import read_json, validate_library, write_json


def main(job_path: Path) -> int:
    job = read_json(job_path)
    work = job_path.parent
    candidate = work / "candidate"
    session = ClaudeSession(work, job["curator"], time.monotonic() + job["timeout_s"],
                            max_calls=1)
    try:
        if job["backend"] == "cc":
            report = curate_cc(session, job["cases"], candidate, Path(job["repo"]))
        elif job["backend"] == "skillopt":
            report = curate_skillopt(session, job["cases"], candidate, Path(job["skillopt_source"]))
        else:
            raise ValueError("Unknown curator backend")
        validate_library(candidate)
        write_json(work / "result.json", {"ok": True, "report": report, "usage": summarize_calls(work)})
        return 0
    except Exception as exc:
        traceback.print_exc()
        write_json(work / "result.json", {"ok": False, "error": str(exc), "usage": summarize_calls(work)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
