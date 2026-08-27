"""Run the mandatory UniVTAC warmup and save per-task memory.

Deliberately minimal: it keeps only the steps that are *required* to
interpret the tactile sensor for this task/machine —

  1. no-contact baseline    open gripper + far-plane depth (~34 mm)
  2. contact probe          step down until first contact, record depth

The scene is assumed to be the server's initial state (the server resets
at startup; agents have no ``reset()`` — see robo.SKILL.md). If the
baseline depth reads far off the far-plane value, the scene is NOT
initial — the warmup warns and the operator must restart the server.

Everything else (initial observation, object poses, empty-close, motion
probe) is optional and omitted: the agent reads scene and command->motion
information from its own task-time observations, which SKILL.md already
mandates doing in small first steps. Thresholds are recorded as data so
the agent can read per-task values (e.g. far-plane depth) instead of
magic numbers.
"""

from __future__ import annotations

# Module-level ``import robo`` (NOT ``from robo import ...``): robo imports
# tools._config, which pulls this module in while robo is still initializing;
# binding the names here would fail on the partial module.  Access the
# attributes at call time instead (robo is fully loaded by then).
import robo
from ._config import load_config, task_name, server_url, save_memory


def run_warmup(
    step_down_m: float = 0.005,
    max_contact_steps: int = 20,
    backoff_m: float = 0.03,
    save: bool = True,
    verbose: bool = True,
) -> dict:
    """Execute the minimal mandatory warmup and (by default) save memory.

    Args:
        step_down_m: contact-probe step size.
        max_contact_steps: contact-probe cap.
        backoff_m: how far to lift after first contact.
        save: write ``memory/warmup_<task>.json`` when True.
        verbose: print progress lines.

    Returns:
        The full warmup record dict (same structure that is saved to memory).
    """
    task = task_name()
    record = {
        "fingerprint": {
            "task": task,
            "server_url": server_url(),
            "api_module": load_config().get("api_module"),
        },
        "task": task,
        "notes": [],
        "steps": {},
        "learned": {},
        "api_rejections": [],
    }

    def note(msg):
        if verbose:
            print(f"[NOTE] {msg}")
        record["notes"].append(str(msg))

    def safe(fn, *a, **kw):
        try:
            return fn(*a, **kw), None
        except Exception as e:
            record["api_rejections"].append(
                f"{getattr(fn, '__name__', fn)}: {type(e).__name__}: {str(e)[:200]}"
            )
            return None, e

    def snap(name, td, extra=None):
        entry = {
            "depth_mm": {"left": td.get("left_depth_mm"), "right": td.get("right_depth_mm")},
            "contact": {"left": td.get("left_contact"), "right": td.get("right_contact")},
            "marker_displacement_px": {
                "left": td.get("left_marker_displacement_px"),
                "right": td.get("right_marker_displacement_px"),
            },
            "marker_coherence": {
                "left": td.get("left_marker_coherence"),
                "right": td.get("right_marker_coherence"),
            },
        }
        if extra:
            entry.update(extra)
        record["steps"][name] = entry
        if verbose:
            print(
                f"[SNAP] {name}: depth L={entry['depth_mm']['left']} "
                f"R={entry['depth_mm']['right']} "
                f"contact L={entry['contact']['left']} R={entry['contact']['right']}"
            )
        return entry

    if verbose:
        print(f"=== warmup {task} ===")

    # --- initial-state probe: read tactile BEFORE any motion ---
    # No reset() available (see robo.py): the scene is whatever the server
    # left it in. Some tasks' reset already grasps the target (insert_HDMI's
    # pre_move holds the prism just above the slot), so the first move must
    # NOT be open_gripper — it would drop the held object. Probe first and
    # branch on the initial state.
    td = robo.get_tactile_data()
    bl = (td["left_depth_mm"] + td["right_depth_mm"]) / 2.0
    if bl < 30.0:
        # Gripper already in contact — it is holding something.
        snap("initial_hold", td)
        note(f"initial state: gripper in contact (depth ~{bl:.1f} mm) — "
             f"an object is already grasped.")
        note("Skipping open_gripper and the contact probe: opening would "
             "drop the held object. Confirm with get_observation() what is "
             "held, then act accordingly (e.g. lower it into place).")
        record["learned"]["contact"] = {
            "initial_hold": True,
            "hold_depth_mm": bl,
            "probe_step_m": None,
            "depth_at_first_contact_mm": None,
            "pattern": (
                f"gripper already grasps the target (depth ~{bl:.1f} mm); "
                f"insertion hardens contact toward the ~20 mm jam level"
            ),
        }
        if save:
            p = save_memory(record, task)
            if verbose:
                print(f"\nSaved warmup memory -> {p}")
                print(f"api_rejections: {record['api_rejections']}")
                print("=== warmup done (initial hold) ===")
        return record

    # --- empty hand: open gripper, no-contact baseline ---
    _, err = safe(robo.open_gripper)
    if err:
        note(f"open_gripper failed: {err}")
    td = robo.get_tactile_data()
    snap("no_contact_baseline", td)
    bl = (td["left_depth_mm"] + td["right_depth_mm"]) / 2.0
    note(f"far-plane depth ~ {bl:.1f} mm (expect ~34 no contact)")
    if bl < 30.0:
        note(f"WARNING: baseline {bl:.1f} mm is far below the far-plane "
             f"value — the scene is not in its initial state. Ask the "
             f"operator to restart the server; do NOT proceed blind.")

    # --- contact probe: step down slowly until detect_contact ---
    note(f"contact probe: stepping down in {step_down_m * 1000:.1f} mm steps "
         f"until detect_contact()")
    steps = 0
    first_contact = None
    while steps < max_contact_steps:
        td, err = safe(robo.move_delta, dz=-step_down_m)
        if err:
            note(f"contact probe move failed: {err}")
            break
        steps += 1
        if robo.detect_contact():
            first_contact = (td["left_depth_mm"] + td["right_depth_mm"]) / 2.0
            snap("first_contact", td, {"steps_down": steps})
            note(f"contact at step {steps}; mean depth {first_contact:.1f} mm")
            break
    if first_contact is None:
        note(f"no contact after {steps} steps ({steps * step_down_m * 1000:.0f} mm) "
             f"— backed off")
        safe(robo.move_delta, dz=backoff_m + 0.02)
    else:
        safe(robo.move_delta, dz=backoff_m)  # back off after contact

    record["learned"]["contact"] = {
        "probe_step_m": step_down_m,
        "depth_at_first_contact_mm": first_contact,
        "pattern": "depth_mm decreases from ~34 (far plane) as contact hardens; "
                   "~27 mm light grasp",
    }

    # --- save ---
    if save:
        p = save_memory(record, task)
        if verbose:
            print(f"\nSaved warmup memory -> {p}")
            print(f"api_rejections: {record['api_rejections']}")
            print("=== warmup done ===")
    return record


def ensure_warmup(force: bool = False, **kw) -> dict:
    """Return existing warmup memory, or run warmup when it is missing.

    Mirrors SKILL.md: if ``memory/warmup_<task>.json`` exists, read and use
    it; otherwise run :func:`run_warmup` and save. Pass ``force=True`` to
    re-run.
    """
    from ._config import load_memory

    mem = None if force else load_memory()
    if mem is not None:
        if kw.pop("verbose", True):
            print(f"[warmup] using existing memory for {task_name()}")
        return mem
    return run_warmup(**kw)
