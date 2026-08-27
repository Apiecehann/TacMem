#!/usr/bin/env python3
"""Recover episode MP4s from scratch frames after the server died.

The UniVTAC serving server writes every captured frame as a PNG into a
scratch directory (``frames_root()``, default ``/tmp/univtac_frames``,
overridable with ``UNIVTAC_FRAMES_DIR``) the moment it is taken, so frames
survive even a SIGKILL — but ``episode.mp4`` is only assembled when the
server finalizes (``episode_video()`` / next reset / normal shutdown), and
the scratch frames are then deleted.  If the server was killed before
finalizing, this script turns the leftover scratch frames into
``episode.mp4`` (+ ``index.json``) under the recording root
(``recording_root()``, default ``<cap-x>/outputs/agent``), then removes
the scratch frames — mirroring a normal finalize.

Usage::

    python scripts/assemble_univtac_video.py <episode_dir> [--fps 10]
    python scripts/assemble_univtac_video.py <task>      # all episodes of a task
    python scripts/assemble_univtac_video.py --all       # every episode recorded

Idempotent: an episode that already has ``episode.mp4`` is skipped unless
``--force`` is given.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Allow running this script standalone (scripts/ is on sys.path, but the
# module lives in capx/serving/).
try:
    from capx.serving.univtac_video import assemble_mp4, frames_root, recording_root
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capx" / "serving"))
    from univtac_video import assemble_mp4, frames_root, recording_root


def _scratch_episodes() -> list[Path]:
    """Return scratch episode dirs (containing ``frames/``) under frames_root()."""
    root = frames_root()
    if not root.is_dir():
        return []
    out = []
    for task_dir in sorted(root.iterdir()):
        if not task_dir.is_dir() or not task_dir.name.startswith("univtac_"):
            continue
        for ts_dir in sorted(task_dir.iterdir()):
            if (ts_dir / "frames").is_dir():
                out.append(ts_dir)
    return out


def assemble_scratch(ts_dir: Path, *, fps: int, force: bool) -> dict:
    """Assemble one scratch episode into the recording root, then clean up.

    Returns a result dict with keys ``skipped`` / ``mp4`` / ``frames`` /
    ``error`` (mirrors ``EpisodeRecorder.finalize``'s shapes).
    """
    ts_dir = Path(ts_dir)
    frames_dir = ts_dir / "frames"
    task = ts_dir.parent.name.removeprefix("univtac_")
    ep_dir = recording_root() / f"univtac_{task}" / ts_dir.name
    mp4 = ep_dir / "episode.mp4"
    if mp4.exists() and not force:
        return {"task": task, "ts": ts_dir.name, "mp4": str(mp4), "skipped": True}
    try:
        if not any(frames_dir.glob("frame_*.png")):
            raise FileNotFoundError(f"no frames in {frames_dir}")
        ep_dir.mkdir(parents=True, exist_ok=True)
        assemble_mp4(frames_dir, mp4, fps=fps)
        idx = ep_dir / "index.json"
        if not idx.exists() or idx.stat().st_size == 0:
            # No index survived (labels live in server memory) — write frame
            # placeholders; the MP4 itself is the durable record.
            n = len(list(frames_dir.glob("frame_*.png")))
            idx.write_text(
                json.dumps(
                    [{"frame": i, "label": f"frame{i}", "t_ms": 0} for i in range(n)],
                    indent=2,
                )
            )
    except Exception as exc:
        return {"task": task, "ts": ts_dir.name, "error": str(exc)}
    # The MP4 exists — drop the scratch frames, like finalize() would.
    # Only ever delete under the scratch root: a caller may point the
    # script at an old-layout episode dir inside the recording root,
    # whose frames/index.json we must not delete.
    n_frames = len(list(frames_dir.glob("frame_*.png")))
    cleaned = False
    if ts_dir.is_relative_to(frames_root()):
        shutil.rmtree(ts_dir, ignore_errors=True)
        cleaned = True
    return {
        "task": task,
        "ts": ts_dir.name,
        "mp4": str(mp4),
        "frames": n_frames,
        "cleaned": cleaned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover episode MP4s from scratch frames (idempotent)."
    )
    parser.add_argument(
        "target", nargs="?",
        help="episode dir (with frames/), task name, or omit for all",
    )
    parser.add_argument(
        "--fps", type=int, default=10,
        help="playback frame rate (default 10)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="re-assemble even if episode.mp4 already exists",
    )
    args = parser.parse_args()

    if args.target:
        # Resolve against the scratch root first, then the recording root
        # (old-layout episodes had their frames inside the recording root).
        raw = Path(args.target)
        candidates = [raw]  # as given (absolute, or relative to the cwd)
        if not raw.is_absolute():
            candidates += [frames_root() / raw, recording_root() / raw]
        target = next((c for c in candidates if (c / "frames").is_dir() or c.is_dir()), None)
        if target is None:
            # A bare task name: frames_root()/univtac_<task>/.
            t = frames_root() / f"univtac_{args.target}"
            target = t if t.is_dir() else None
        if target is None:
            print(f"error: {args.target} is not a task or episode dir "
                  f"(scratch frames under {frames_root()}, recordings under "
                  f"{recording_root()})")
            return 1
        if (target / "frames").is_dir():
            episodes = [target]
        else:  # a task dir: univtac_<task>/
            episodes = [d for d in sorted(target.iterdir()) if (d / "frames").is_dir()]
    else:
        episodes = _scratch_episodes()

    if not episodes:
        print(f"no episode dirs (with frames/) found under {frames_root()}")
        return 1

    ok = skipped = failed = 0
    for ep in episodes:
        result = assemble_scratch(ep, fps=args.fps, force=args.force)
        if result.get("skipped"):
            skipped += 1
            print(f"  {ep.name}: skipped (already has episode.mp4)")
        elif result.get("error"):
            failed += 1
            print(f"  {ep.name}: FAILED: {result['error']}")
        else:
            ok += 1
            extra = "scratch cleaned" if result.get("cleaned") else "source kept"
            print(f"  {ep.name}: -> {result['mp4']} "
                  f"({result.get('frames', '?')} frames, {extra})")
    print(f"done: {ok} assembled, {skipped} skipped, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
