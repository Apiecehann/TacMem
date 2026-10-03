"""Tactile event features and the stateful event detector for the fast loop.

The fast loop (``move_reflexive`` in launch_univtac_server.py) evaluates
tactile events at control frequency while a motion runs, and freezes the
arm at the first enabled event.  This module is the pure, simulation-free
core: feature extraction from raw sensor tensors, event detection, and the
stop decision.  It imports nothing from Isaac / UniVTAC so it is fully
unit-testable on CPU.

Signal model (GelSight Mini, per fingertip):

  - ``marker``:  [2, M, 2] tensor — [initial, current] marker positions
    x [x, y] UV pixels.  Displacement and shear are measured against the
    reset-time reference (the no-contact baseline; the sim-internal
    "initial" frame carries a constant per-finger offset and is useless).
  - ``depth``:  [H, W] GelSight height map in mm.  34 mm is the far plane
    (no contact); depth DECREASES as contact hardens (~27 mm light grasp,
    ~20 mm hard jam).
  - ``mean_vec`` = mean per-marker displacement vector — the coherent
    (shear) part.  Coherence = |mean_vec| / mean per-marker magnitude,
    high when markers move together (shear/slip), low when they deform
    incoherently (compression, tracking loss).

Event vocabulary (all ORed across sides; only events present in the
``stop_on`` dict are enabled)::

    contact       first touch (depth < far_plane - 0.1)
    hard_depth    depth <= 20 mm — hard jam / overpressure
    shear_spike   per-step displacement GROWTH >= 2 px with coherence >= 0.7
    slip          shear direction turns >= 30 deg between steps (>= 2
                  samples; magnitude noise gate 2 px)
    plateau       contact flat (depth change <= 0.5 mm over the last 5
                  steps, in contact, pressing below 27.5 mm) — jammed /
                  seated
    asymmetry     |L_depth - R_depth| >= 3 mm with at least one side in
                  contact — yaw misalignment

Thresholds are module constants (single source of truth); ``stop_on`` may
override any of them (numeric values override, booleans just enable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import torch

DEFAULT_THRESHOLDS: dict[str, float] = {
    "far_plane_mm": 34.0,     # gsmini cfg.robot.tactile_far_plane
    "hard_depth_mm": 20.0,    # documented hard jam
    "light_grasp_mm": 27.5,   # adaptive_grasp_depth_threshold
    "shear_spike_px": 2.0,    # detect_slip default threshold_px
    "coherence": 0.7,         # detect_slip default coherence gate
    "slip_turn_deg": 30.0,
    "asymmetry_mm": 3.0,
    "plateau_mm": 0.5,
    "plateau_steps": 5,
}

EVENT_NAMES = ("contact", "hard_depth", "shear_spike", "slip", "plateau", "asymmetry")

# stop_on key (event short name) -> DEFAULT_THRESHOLDS key it overrides
# with a numeric value (booleans just enable the event, not thresholds).
# Keys are short names in the request, long names in the defaults dict.
_THRESHOLD_ALIASES = {
    "contact": "far_plane_mm",
    "hard_depth": "hard_depth_mm",
    "shear_spike": "shear_spike_px",
    "slip": "slip_turn_deg",
    "asymmetry": "asymmetry_mm",
}

# Stop-decision priority when several events fire in one step (most severe
# first; max_steps is a separate safety net, evaluated after these).
_SEVERITY_ORDER = ("hard_depth", "slip", "shear_spike", "plateau", "asymmetry", "contact")


@dataclass
class SideFeatures:
    """Per-fingertip tactile features for one evaluation step.

    ``valid`` is False when the side's marker or depth data is missing;
    the detector skips such sides (no crash, no event).
    """

    valid: bool = False
    contact: bool = False
    depth_mm: float | None = None
    shear_dx: float = 0.0        # mean displacement vector x (px) — coherent shear
    shear_dy: float = 0.0
    shear_mag: float = 0.0       # |mean_vec| (px)
    coherence: float = 0.0       # |mean_vec| / mean|delta| — same formula as _marker_stats
    mean_disp: float = 0.0       # mean per-marker |delta| (px) — identical to marker_displacement_px


def extract_side_features(marker: torch.Tensor | None,
                          reference: torch.Tensor | None,
                          depth: torch.Tensor | None,
                          far_plane_mm: float) -> SideFeatures:
    """Compute per-side tactile features from the raw sensor tensors.

    Args:
        marker: [2, M, 2] tensor ([initial, current] x [x, y]); None when
            the side has no data.
        reference: [M, 2] reset-time marker positions (no-contact
            baseline); None falls back to marker[0] (same semantics as
            _sim_get_tactile_data).
        depth: [H, W] GelSight height map in mm; None when unavailable.
        far_plane_mm: depth below which contact is declared (sensor
            far-plane constant, ~34.0).

    Returns:
        SideFeatures with valid=False when marker or depth is missing.
    """
    if marker is None or depth is None:
        return SideFeatures()
    base = reference if reference is not None else marker[0]
    delta = (marker[1] - base).float()          # [M, 2]
    mag = delta.pow(2).sum(-1).sqrt()           # [M] per-marker L2
    mean_vec = delta.mean(dim=0)                # coherent (shear) part
    mean_mag = float(mag.mean())
    coherence = (
        float(torch.norm(mean_vec) / mean_mag) if mean_mag > 1e-6 else 0.0
    )
    return SideFeatures(
        valid=True,
        contact=float(depth.min()) < far_plane_mm - 0.1,
        depth_mm=float(depth.min()),
        shear_dx=float(mean_vec[0]),
        shear_dy=float(mean_vec[1]),
        shear_mag=float(torch.norm(mean_vec)),
        coherence=coherence,
        mean_disp=mean_mag,  # identical to marker_displacement_px (per-marker L2 mean)
    )


def _merge_thresholds(stop_on: dict | None) -> dict[str, float]:
    """DEFAULT_THRESHOLDS overlaid with any numeric overrides in stop_on."""
    th = dict(DEFAULT_THRESHOLDS)
    if not stop_on:
        return th
    for key, value in stop_on.items():
        if key in _THRESHOLD_ALIASES and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            th[_THRESHOLD_ALIASES[key]] = float(value)
        elif key == "plateau" and isinstance(value, dict):
            if "mm" in value:
                th["plateau_mm"] = float(value["mm"])
            if "steps" in value:
                th["plateau_steps"] = float(value["steps"])
    return th


def _enabled(stop_on: dict, event: str) -> bool:
    """True when *event* is explicitly enabled in stop_on.

    A key present with True, or with any numeric threshold value (e.g.
    hard_depth=20.0), enables the event; plateau is enabled by its dict.
    """
    value = stop_on.get(event)
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return True
    if event == "plateau" and isinstance(value, dict):
        return True
    return False


class TactileEventDetector:
    """Stateful detector over a rolling per-side feature history.

    Instantiate one per motion attempt; call :meth:`update` once per step
    with both sides' features.  Events are recorded once (first-fire, in
    the order first observed); :attr:`latest_events` holds only the events
    of the most recent update.
    """

    def __init__(self, stop_on: dict | None = None,
                 far_plane_mm: float = DEFAULT_THRESHOLDS["far_plane_mm"]) -> None:
        self.thresholds = _merge_thresholds(stop_on)
        # A numeric "contact" override redefines the far plane, so feature
        # extraction and detection share the same merged value; otherwise
        # the explicit far_plane_mm argument applies.
        if "contact" not in (stop_on or {}):
            self.thresholds["far_plane_mm"] = far_plane_mm
        self.far_plane_mm = float(self.thresholds["far_plane_mm"])
        self.events: list[str] = []          # every event ever fired, deduped
        self.n_updates = 0
        self.contact_step: int | None = None
        self.max_shear_px = 0.0
        self.max_asymmetry_mm = 0.0
        self.min_depth_mm = float("inf")
        self._latest: list[str] = []
        self._shear_dir_deg: float | None = None
        self._hist: dict[str, list[SideFeatures]] = {"left": [], "right": []}
        self._window = max(3, int(self.thresholds["plateau_steps"]))

    def update(self, left: SideFeatures | None,
               right: SideFeatures | None) -> list[str]:
        """Evaluate one step of tactile data; return the events fired.

        Args:
            left / right: per-side features for this step (None or
                valid=False sides are skipped; their rolling history is
                cleared so plateau windows do not span data gaps).

        Returns:
            The event names that fired at this step, in detection order.
        """
        self.n_updates += 1
        sides = {"left": left, "right": right}
        fired: list[str] = []

        for name, side in sides.items():
            if side is None or not side.valid:
                self._hist[name].clear()
                continue
            hist = self._hist[name]
            hist.append(side)
            if len(hist) > self._window:
                del hist[:len(hist) - self._window]

            # contact / hard_depth — direct depth thresholds
            if side.contact:
                if self.contact_step is None:
                    self.contact_step = self.n_updates
                fired.append("contact")
            if side.depth_mm is not None and side.depth_mm <= self.thresholds["hard_depth_mm"]:
                fired.append("hard_depth")

            # shear spike — per-step displacement GROWTH, coherence-gated
            if len(hist) >= 2:
                growth = abs(side.mean_disp - hist[-2].mean_disp)
                if growth >= self.thresholds["shear_spike_px"] \
                        and side.coherence >= self.thresholds["coherence"]:
                    fired.append("shear_spike")

            # slip — direction turn of the coherent shear vector between
            # consecutive steps, with a magnitude noise gate (>= 2 px)
            if len(hist) >= 2 and side.shear_mag >= self.thresholds["shear_spike_px"]:
                v0x, v0y = hist[-2].shear_dx, hist[-2].shear_dy
                m0 = math.hypot(v0x, v0y)
                if m0 > 1e-6:
                    cos_a = (v0x * side.shear_dx + v0y * side.shear_dy) / (m0 * side.shear_mag)
                    turn = math.degrees(math.acos(max(-1.0, min(1.0, cos_a))))
                    if turn >= self.thresholds["slip_turn_deg"] \
                            and side.coherence >= self.thresholds["coherence"]:
                        fired.append("slip")

            # plateau — contact flat (jammed/seated): last N steps in
            # contact, genuinely pressing (<= light grasp), depth spread
            # below the plateau budget
            ps = int(self.thresholds["plateau_steps"])
            if len(hist) >= ps:
                window = hist[-ps:]
                if all(w.contact and w.depth_mm is not None
                       and w.depth_mm <= self.thresholds["light_grasp_mm"]
                       and w.depth_mm < self.far_plane_mm - 0.1 for w in window):
                    depths = [w.depth_mm for w in window]
                    if max(depths) - min(depths) <= self.thresholds["plateau_mm"]:
                        fired.append("plateau")

        # asymmetry — across sides; only meaningful when both are valid and
        # at least one is in contact (far-plane-vs-far-plane noise is not)
        if (left is not None and right is not None and left.valid and right.valid
                and left.depth_mm is not None and right.depth_mm is not None
                and (left.contact or right.contact)):
            asym = abs(left.depth_mm - right.depth_mm)
            if asym >= self.thresholds["asymmetry_mm"]:
                fired.append("asymmetry")

        # running maxima (digest)
        for side in (left, right):
            if side is not None and side.valid:
                self.max_shear_px = max(self.max_shear_px, side.shear_mag)
                if side.depth_mm is not None:
                    self.min_depth_mm = min(self.min_depth_mm, side.depth_mm)
        if (left is not None and right is not None and left.valid and right.valid
                and left.depth_mm is not None and right.depth_mm is not None):
            self.max_asymmetry_mm = max(
                self.max_asymmetry_mm, abs(left.depth_mm - right.depth_mm)
            )

        # dominant-side shear direction at this step (sensor UV frame)
        dominant = None
        for side in (left, right):
            if side is not None and side.valid and (dominant is None
                                                    or side.shear_mag > dominant.shear_mag):
                dominant = side
        if dominant is not None and dominant.shear_mag > 1e-6:
            self._shear_dir_deg = math.degrees(
                math.atan2(dominant.shear_dy, dominant.shear_dx)
            )

        self._latest = fired
        for e in fired:
            if e not in self.events:
                self.events.append(e)
        return fired

    @property
    def latest_events(self) -> list[str]:
        """Events fired at the most recent update (detection order)."""
        return list(self._latest)

    @property
    def shear_dir_deg(self) -> float | None:
        """atan2(dy, dx) of the dominant-side shear vector (sensor UV frame)."""
        return self._shear_dir_deg

    @property
    def summary(self) -> dict[str, Any]:
        """The event digest: running maxima + first-fire event list."""
        return {
            "max_shear_px": round(self.max_shear_px, 3),
            "shear_dir_deg": (round(self._shear_dir_deg, 1)
                              if self._shear_dir_deg is not None else None),
            "asymmetry_mm": round(self.max_asymmetry_mm, 3),
            "min_depth_mm": (round(self.min_depth_mm, 3)
                             if math.isfinite(self.min_depth_mm) else None),
            "contact_step": self.contact_step,
            "events": list(self.events),
        }


def evaluate(stop_on: dict | None, detector: TactileEventDetector
             ) -> tuple[bool, str | None]:
    """Decide whether the motion should stop after this step's events.

    Args:
        stop_on: conditions dict (None = monitoring mode: never stop, the
            digest is still populated).  Only keys present in the dict
            enable their condition; when several fire in one step the most
            severe wins (hard_depth > slip > shear_spike > plateau >
            asymmetry > contact).  max_steps is a safety net: it stops the
            motion once at least that many evaluations happened and no
            enabled event fired this step.
        detector: the stateful detector for the current attempt.

    Returns:
        (True, reason) when the motion must stop, else (False, None).
        reason is an event name or "max_steps".
    """
    if not stop_on:
        return False, None
    for event in _SEVERITY_ORDER:
        if event in detector.latest_events and _enabled(stop_on, event):
            return True, event
    max_steps = stop_on.get("max_steps")
    if max_steps and detector.n_updates >= int(max_steps):
        return True, "max_steps"
    return False, None
