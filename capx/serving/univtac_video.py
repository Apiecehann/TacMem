"""Episode video recording for the UniVTAC serving server.

The server captures a composite frame (head camera + wrist camera +
left/right tactile, 320x1120) before/after every API action, plus every few
servo iterations inside a lead-coupled helix twist -- that one drives its own
IK loop with no per-step action boundary, so it has nowhere else to hang a
frame.  Keeping those frames only in process memory made it impossible to
review an agent's run after the fact (the server restart on the previous run
lost everything).
This module turns that capture into a durable record:

  * each captured frame is written to disk as a PNG the moment it is taken —
    but into a **scratch** directory (default ``/tmp/univtac_frames``), so
    the long-term recording root only ever holds the final products;
  * on episode end (``episode_video()``, the next reset, or server shutdown)
    the PNG sequence is assembled into ``episode.mp4`` with ffmpeg, plus an
    ``index.json`` that labels every frame with the API action that produced
    it, and the scratch frames are deleted.  A SIGKILL before finalizing
    leaves the scratch frames on disk for
    ``scripts/assemble_univtac_video.py`` to recover.

Recording is on by default and can be switched off / relocated via env:

  * ``UNIVTAC_RECORD=0``        disable recording entirely
  * ``UNIVTAC_RECORD_DIR=…``    recording root (default ``<cap-x>/outputs/agent``)
  * ``UNIVTAC_FRAMES_DIR=…``    scratch frame root (default ``/tmp/univtac_frames``)

Layout mirrors the eval harness's ``outputs/<model>/univtac_<task>/``
convention: episodes live under ``outputs/agent/univtac_<task>/<ts>/`` with
the same ``%Y%m%d_%H%M%S`` timestamp format; each episode directory contains
only ``episode.mp4`` + ``index.json`` (+ the action log written by
``univtac_action_log.py``).
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

# Single "frames -> MP4" implementation, shared with the eval harness.
try:
    from capx.utils.video_utils import encode_frames_to_mp4
except ImportError:  # direct-script run: put the cap-x root on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from capx.utils.video_utils import encode_frames_to_mp4

logger = logging.getLogger("univtac_video")


def recording_enabled() -> bool:
    """Return True unless recording was disabled via ``UNIVTAC_RECORD=0``."""
    return os.environ.get("UNIVTAC_RECORD", "1") != "0"


def recording_root() -> Path:
    """Return the recording root (``UNIVTAC_RECORD_DIR`` override or default)."""
    env = os.environ.get("UNIVTAC_RECORD_DIR")
    if env:
        return Path(env)
    # This file lives at <cap-x>/capx/serving/univtac_video.py.
    # Same outputs/ convention as the eval harness (outputs/<model>/...);
    # the model slot is the fixed name "agent".
    return Path(__file__).resolve().parents[2] / "outputs" / "agent"


def frames_root() -> Path:
    """Return the scratch frame root (``UNIVTAC_FRAMES_DIR`` override or default).

    Frames are written here — NOT into the episode dir — so the long-term
    recording root only contains the final products.  They are on disk
    anyway (surviving a SIGKILL, for recovery by
    ``scripts/assemble_univtac_video.py``), just outside the recording root;
    ``finalize()`` deletes them once the MP4 exists.
    """
    env = os.environ.get("UNIVTAC_FRAMES_DIR")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "univtac_frames"


def assemble_mp4(frames_dir: Path, out_mp4: Path, *, fps: int = 10) -> Path:
    """Assemble a ``frame_%05d.png`` sequence into an MP4.

    Loads the PNGs into memory and delegates to the repo-wide
    ``encode_frames_to_mp4`` (capx.utils.video_utils) — the single
    "frames -> MP4" implementation shared with the eval harness.

    Args:
        frames_dir: directory containing ``frame_00000.png``, ``frame_00001.png``, …
        out_mp4: destination MP4 path.
        fps: playback frame rate (default 10).

    Returns:
        The written ``out_mp4`` path.

    Raises:
        FileNotFoundError: no frames found in ``frames_dir``.
    """
    frames_dir = Path(frames_dir)
    out_mp4 = Path(out_mp4)
    pngs = sorted(frames_dir.glob("frame_*.png"))
    if not pngs:
        raise FileNotFoundError(f"no frames in {frames_dir}")
    frames = [np.asarray(Image.open(p).convert("RGB")) for p in pngs]
    encode_frames_to_mp4(frames, out_mp4, fps=fps)
    return out_mp4


class EpisodeRecorder:
    """Durable per-episode frame recorder.

    Frames go to a scratch directory (``frames_root()``) and are deleted
    when ``finalize()`` succeeds; the recording root holds only the final
    ``episode.mp4`` + ``index.json``.

    Lifecycle::

        rec = EpisodeRecorder(recording_root())
        rec.start(task, seed)     # new episode (frames go to scratch)
        rec.record(b64_png, label)  # one frame per action
        rec.finalize(fps=10)      # episode.mp4 + index.json; frames deleted
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        frames_root: Path | None = None,
        enabled: bool | None = None,
    ):
        self._root = Path(root) if root is not None else recording_root()
        self._frames_root = (
            Path(frames_root) if frames_root is not None else frames_root()
        )
        self._enabled = recording_enabled() if enabled is None else enabled
        self._episode_dir: Path | None = None
        self._frames_dir: Path | None = None
        self._index: list[dict] = []
        self._frame_count = 0
        self._served = 0  # frames already handed to the eval harness
        self._harness_mode = False  # eval harness consumes frames itself
        self._task = ""
        self._seed = 0
        self._started_ts = ""
        self._last: dict | None = None

    @property
    def active(self) -> bool:
        return self._episode_dir is not None and self._frame_count > 0

    def start(self, task: str, seed: int) -> dict:
        """Open a new episode directory (timestamped) for ``task``.

        When recording is disabled this only clears state and returns an
        info dict with ``dir=None`` — no directory is created.
        """
        self._task = task
        self._seed = seed
        self._started_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._index = []
        self._frame_count = 0
        self._served = 0
        if not self._enabled:
            self._episode_dir = None
            self._frames_dir = None
            return {
                "task": self._task,
                "seed": self._seed,
                "started": self._started_ts,
                "dir": None,
                "frames_dir": None,
                "mp4": None,
                "index_json": None,
                "frames": 0,
                "mp4_ready": False,
            }
        # ``univtac_<task>`` task folder, mirroring the eval harness
        # (outputs/<model>/univtac_<task>/); episodes are timestamped.
        # The episode dir itself is NOT created here — finalize() writes
        # episode.mp4 / index.json into it, and until then the recording
        # root stays untouched.  Frames go to the scratch frames_root.
        self._episode_dir = self._root / f"univtac_{task}" / self._started_ts
        self._frames_dir = (
            self._frames_root / f"univtac_{task}" / self._started_ts / "frames"
        )
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        return {
            "task": self._task,
            "seed": self._seed,
            "started": self._started_ts,
            "dir": str(self._episode_dir),
            "frames_dir": str(self._frames_dir),
            "mp4": str(self._episode_dir / "episode.mp4"),
            "index_json": str(self._episode_dir / "index.json"),
            "frames": self._frame_count,
            "mp4_ready": (self._episode_dir / "episode.mp4").exists(),
        }

    def set_harness_mode(self) -> None:
        """Switch to harness mode: the eval harness fetches frames itself
        (``drain_frames()``) and assembles the trial videos into its own
        output directory, so ``finalize()`` stops writing
        ``episode.mp4``/``index.json`` into the agent recording root
        (``outputs/agent``).  One-way: once an eval consumes frames on a
        server, that server stops self-finalizing episodes.
        """
        self._harness_mode = True

    @property
    def harness_mode(self) -> bool:
        """True once the eval harness started consuming frames itself."""
        return self._harness_mode

    def drain_frames(self) -> list[bytes]:
        """Return the active episode's unserved frames as PNG bytes.

        Each frame is returned at most once (the eval harness fetches once
        per trial with ``clear=True`` semantics); the durable recorder and
        the scratch PNGs are untouched.
        """
        if not self._enabled or self._frames_dir is None:
            return []
        pngs = sorted(self._frames_dir.glob("frame_*.png"))
        out = [p.read_bytes() for p in pngs[self._served:]]
        self._served = len(pngs)
        return out

    def record(self, b64_png: str, label: str) -> int:
        """Persist one frame (base64 PNG) plus its action label.

        Returns the frame index, or -1 if recording is disabled or no
        episode is active.
        """
        if not self._enabled or self._episode_dir is None or self._frames_dir is None:
            return -1
        idx = self._frame_count
        try:
            img = Image.open(io.BytesIO(base64.b64decode(b64_png)))
            img.save(self._frames_dir / f"frame_{idx:05d}.png")
        except Exception as exc:  # disk full / permission — degrade, never crash the API
            logger.warning("frame write failed (skipping frame %d): %s", idx, exc)
            return -1
        self._index.append(
            {"frame": idx, "label": label, "t_ms": int(time.time() * 1000)}
        )
        self._frame_count += 1
        return idx

    def finalize(self, *, fps: int = 10) -> dict | None:
        """Assemble the current episode into ``episode.mp4`` + ``index.json``.

        Writes the products into the episode dir (created here), then
        deletes the scratch frames.  On failure the scratch frames are
        kept, so the MP4 can still be recovered afterwards with
        ``scripts/assemble_univtac_video.py``.  Safe to call repeatedly:
        once finalized (or with no frames) it is a no-op that returns the
        last result.
        """
        if not self._enabled:
            return None
        if self._harness_mode:
            # The eval harness drained the frames and assembled the trial
            # video itself — drop the scratch and never write into the
            # agent recording root.
            if self._frames_dir is not None:
                shutil.rmtree(self._frames_dir, ignore_errors=True)
            self._episode_dir = None
            self._frames_dir = None
            return None
        if not self.active:
            return None
        episode_dir = self._episode_dir
        frames_dir = self._frames_dir
        self._episode_dir = None
        self._frames_dir = None
        try:
            episode_dir.mkdir(parents=True, exist_ok=True)
            assemble_mp4(frames_dir, episode_dir / "episode.mp4", fps=fps)
            (episode_dir / "index.json").write_text(
                json.dumps(self._index, indent=2)
            )
            self._last = {
                "task": self._task,
                "seed": self._seed,
                "started": self._started_ts,
                "dir": str(episode_dir),
                "frames_dir": str(frames_dir),
                "mp4": str(episode_dir / "episode.mp4"),
                "index_json": str(episode_dir / "index.json"),
                "frames": self._frame_count,
                "fps": fps,
            }
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("video finalize failed for %s: %s", episode_dir, exc)
            self._last = {"dir": str(episode_dir), "error": str(exc)}
            return self._last  # keep the scratch frames for recovery
        # The MP4 exists — the frames were scratch; drop them.
        try:
            shutil.rmtree(frames_dir, ignore_errors=True)
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("scratch frame cleanup failed for %s: %s", frames_dir, exc)
        return self._last

    def info(self) -> dict | None:
        """Info for the active episode, or the last finalized one."""
        if self.active:
            return {
                "task": self._task,
                "seed": self._seed,
                "started": self._started_ts,
                "dir": str(self._episode_dir),
                "frames_dir": str(self._frames_dir),
                "mp4": str(self._episode_dir / "episode.mp4"),
                "index_json": str(self._episode_dir / "index.json"),
                "frames": self._frame_count,
                "mp4_ready": (self._episode_dir / "episode.mp4").exists(),
            }
        return self._last
