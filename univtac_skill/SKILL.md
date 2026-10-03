---
name: univtac-skill
description: "Guide the agent to control a Franka robot with GelSight Mini tactile sensors in the UniVTAC simulation. Use when the agent must operate the robot through the importable `robo` package (goto_pose, move_delta, get_tactile_data, get_observation, ...), reading camera images and tactile readings each step, performing a tactile warmup first, and storing warmup memory under univtac_skill/memory."
---

# UniVTAC Tactile Skill

You control a Franka arm with GelSight Mini tactile sensors in the UniVTAC
Isaac Sim simulation, through the `robo` package (a thin client to an
already-running server). **You never talk to the simulator directly, and you
never read the simulator's source.**

## Before anything

- **The environment is already set up.** Run everything — one-liners and
  your scratch/ scripts alike — through `./run_py` (`./run_py -c "..."` or
  `./run_py scratch/x.py`): it picks the right venv, PYTHONPATH and cache
  dirs. Do not hunt for the interpreter or set PYTHONPATH yourself; a
  scratch/ script run with a plain `python` cannot import robo at all.
- Your **goal** is `config.json`'s `task_description` (also exported as
  `robo.TASK_DESCRIPTION`). Read it first and work toward exactly that.
- `from robo import health; health()` must be True. If not, the server is
  down — stop and report it; do not try to start a simulator.
- `robo` must import. If it does not, your launch environment is wrong —
  stop and report it; do not work around it by importing the backend.

## Information boundary (hard limit)

Your only information sources: the `univtac_skill/` directory (this skill,
`config.json`, your warmup memory), and the API's own returns
(`get_observation()`, `get_tactile_data()`, `get_object_pose()`, ...).
Treat the robot and scene as a black box observed only through the API.

Do **not** read, open, grep, or import anything outside `univtac_skill/` —
including `capx/`, `env_configs/`, `task_config/`, UniVTAC sources, logs,
or any project config/dataset. That includes ground-truth task answers
(e.g. which texture is rough): the answer must come from your own
observation, never from a config file. If you lack information, observe —
do not go looking for it.

## API

Import from `robo`:

```python
from robo import (
    health,
    goto_pose, move_delta, move_reflexive, open_gripper, close_gripper, rotate_gripper,
    get_tactile_data, get_tactile_image, detect_contact, detect_slip,
    get_object_pose, sample_grasp_pose,
    get_observation, get_ee_pose, check_success, episode_video,
)
```

`robo.api_doc()` returns every function's full signature and contract — read
it for specifics. Only the functions above are allowed; do not call server
endpoints by hand. The caveats that matter most:

- **Orientation fix-ups**: `rotate_gripper(object, angle_rad)` rotates a
  **held** object about the gripper's own axis — use it to straighten a
  tilted grasp. `move_delta`'s `drx/dry/drz` are **world-frame by default**
  (`coord="world"`): a world-axis rotation swings the whole arm and usually
  makes a tilt worse. Pass `coord="ee"` for tool-frame rotation.
- `goto_pose(..., z_approach=0.1)` for grasps/placements.
- `detect_slip` is **stateful**: the FIRST call only records a baseline and
  returns False. Use it as a pair (baseline, then read after the risky
  motion) in the same script.
- `get_object_pose(name)` is **non-privileged**: `name` is the text prompt
  sent to the SAM3 segmenter — pass `robo.SAM3_PROMPTS[...]` (calibrated
  phrases per object, e.g. `SAM3_PROMPTS["prism"]` → `"black connector"`),
  never the bare semantic name (`"prism"` mis-segments). The returned
  `position` is the **centroid of the segmented point cloud** — noisy
  (±mm), and offset from the object's true origin — so do NOT use it for
  precise alignment correction or as an exact height. `quaternion_wxyz`
  is always identity: **do not read orientation from it**. Each call
  re-runs segmentation (~1 s), so call sparingly: one localization at the
  start, one seating probe at the end. On `RuntimeError` (SAM3 found
  nothing) retry once with a more specific color/material phrase.
- `check_success()` is a **ground-truth oracle**. Prefer judging success
  from your own observation; use it only to confirm at the very end.
- `get_ee_pose()` returns the end-effector pose (`position` + 
  `quaternion_wxyz`, world frame). **Diagnostic only**: verify a commanded
  `move_delta` actually executed, detect drift from a known-good pose,
  re-anchor before a precise descent. Do NOT use it for object alignment —
  `goto_pose` plans in the same frame it returns, and perception (SAM3)
  is far noisier than arm kinematics, so aligning through it adds no
  precision.
- **There is no `reset()`.** The scene starts in the server's initial state
  and never resets while you run. A failure ends your attempt: analyze,
  adapt in place, or report honestly — do not start over.

## SAM3 prompt selection

`get_object_pose`'s argument is a segmentation prompt, so phrase choice
decides what you get back (calibrated defaults live in `robo.SAM3_PROMPTS`
per task). Lessons from calibration runs:

- Bare semantic names mis-segment: `"prism"` catches the socket body
  (same material), `"slot"` catches the gripper. Prefer **discriminative
  geometry**: `"hole"` beats `"slot"` (4 of 5 seeds).
- Small colored objects need a **color/material clue**: `"black connector"`
  works where `"prism"` fails.
- Prefer a prompt that targets one object only; if two objects respond,
  refine with a more specific phrase rather than post-filtering the mask.
- If `get_object_pose` raises `RuntimeError` (nothing segmented), retry
  once with a more specific color/material phrase before treating the
  object as unobservable.

## Resolution matching

The head/wrist views resolve **large-scale state** reliably — where objects
are, whether the gripper is empty or holding something, whether a carried
object released or stayed with the gripper. Fine state — alignment, gaps,
seating contact — is a few pixels across: physically unreadable at full
view. Match the view to the question:

- **Large-scale question** → reason from the full `get_observation()`
  image.
- **Fine question, you know roughly where** → `zoom(cam, box=...)` or
  `zoom(cam, center=...)`: crop the region of interest and magnify it.
- **Fine question, you know WHAT but not where** → `segmented_zoom(prompt)`:
  crops around the SAM3 mask of the prompted object — no pixel guessing.
- **Fine question, still unreadable after zoom** → the state is below the
  camera's physical resolution. Do not guess from a blur: switch signals
  (tactile depth / contact pattern, geometry reasoning, motion response),
  or pick a view/crop that resolves it.
- **Question is about the OBJECT, not the gripper** → tactile cannot
  answer it: far plane means the hand is empty, not that the object is in
  place — a carried object that dropped mid-move reads the same as one
  correctly placed. When the object's fate decides your next step, look
  at it; a tactile read never overrides what the image shows.

## Tactile reading guide

`get_tactile_data()` returns per fingertip (left_ / right_):

| Field | What it means |
|---|---|
| `*_contact` | bool — fingertip touching something |
| `*_depth_mm` | distance to contact surface; **~34 mm = far plane (no contact), DECREASES as contact hardens** (~27 mm light grasp, ~20 mm hard jam) |
| `*_marker_displacement_px` | mean marker motion since the no-contact reset — rises with ANY gel deformation (contact, insertion, slip), so NOT a clean slip signal on its own |
| `*_marker_coherence` | 0–1 uniformity of marker motion — **high = markers move together (shear/slip), low = incoherent (compression/tracking loss)**; combine with displacement for slip |

Stopping on contact (the standard pattern for contact-critical moves):

```python
fb = get_tactile_data()          # or detect_contact()
while not fb["left_contact"] and not fb["right_contact"]:
    fb = move_delta(dz=-0.005)   # small steps, read tactile after each
```

Slip detection (grasp hold test): `detect_slip()` once to set the baseline,
then move, then call again — True means the object sheared.

## Two-loop control (`move_reflexive`, preferred for contact)

Tactile is a fast signal and you are a slow loop; polling between small moves
wastes wall-clock and reacts a step late. `move_reflexive` evaluates the
tactile events INSIDE the motion — the server freezes the arm the moment the
first enabled condition fires, and you read a digest of what it felt:

```python
r = move_reflexive(dz=-0.005, stop_on={"contact": True})   # approach until touch
print(r["terminated_by"], r["event"])                      # "contact", digest
```

**Pick `stop_on` conditions per motion** (all ORed; absent keys stay off;
definitions/defaults/digest schema: `robo.api_doc()` → `move_reflexive`):

- approach until first touch → `{"contact": True}`
- insert/seat until seated → `{"plateau": {"mm": 0.5, "steps": 5}}` — press
  goes flat (jammed or seated)
- press with jam protection → `{"hard_depth": 20}` (mm)
- grasp hold test → `{"slip": 30}` (direction-aware) or `{"shear_spike": 2}`
  (magnitude)
- yaw misalignment → `{"asymmetry": 3}` (mm, |L−R| depth gap)
- safety net → `{"max_steps": 50}`

Rules that matter:

- `success=True` may mean "stopped on a condition", not "reached the target"
  — check `terminated_by` before reasoning about position.
- Read the digest, not just the flag: `terminated_by`, `min_depth_mm` (press
  hardness), `asymmetry_mm` (lopsided?), `shear_dir_deg` (which way the
  surface pulled — plugged vs jammed shear differently), `events` (order).
- A triggered motion is never retried internally; it is not a failure.
- `move_reflexive` replaces the slow while-loop contact pattern above for
  one-shot descents; keep the loop only when each step needs an image or a
  non-reflexive decision.
- Cost: ~2 FPS (each step renders for fresh tactile) — keep deltas ≤ 0.05 m.
  After a reflex stop, back off and re-plan, do not hammer.

Slow loop (decision points only): after a reflex stop — or any moment you
must reason about touch — `get_tactile_data()` for scalars and
`get_tactile_image(side)` for the raw GelSight marker view, saved to
scratch/ and inspected like a camera image. NEVER fetch either mid-motion:
every call is a full render, and the reflex loop already renders each step.

## Reusable helpers (`tools/`, preferred)

Task-agnostic infrastructure so you do not re-implement warmup, step
logging, or camera snapshots each episode. Import with
`from tools import ...`. They are **not** policies: you still decide which
objects, where, in what order, and what "done" means.

- `ensure_warmup()` — use saved warmup memory, or run + save it (see Warmup).
- `StepRecorder(name)` / `snapshot(name)` — per-step tactile/extra JSON log
  and camera snapshots; use instead of hand-rolled LOG lists / PIL saves.
- `zoom(name, cam=..., box=...)` / `segmented_zoom(name, prompt)` — save a
  magnified crop of one camera image to scratch/ and return its path; use
  instead of hand-rolled PIL crops (see Resolution matching). `zoom` takes
  pixel `box=(x0,y0,x1,y1)` or `center=(x,y)` + `size`;
  `segmented_zoom` finds the object's pixels itself (SAM3 mask box, same
  segmenter `get_object_pose` uses) and crops around them.

## Warmup (mandatory, once per task)

Call `ensure_warmup()` — it reuses `memory/warmup_<task>.json` if present,
otherwise runs the minimal warmup and saves it. Warmup first reads the
tactile sensors BEFORE any motion: if the gripper already reads contact
(depth well below the ~34 mm far plane), it records an ``initial_hold``
and skips open-gripper / contact probing — an object is already grasped,
and opening would drop it. On an empty hand it runs the usual no-contact
baseline → contact probe. Warmup output is per-machine and gitignored.

Everything else — scene snapshot, object poses, empty-hand close,
command→motion calibration — is deliberately **not** part of warmup: you
observe the scene and verify motion yourself in small first steps.

## Control workflow

1. The scene starts in the server's initial state; there is no `reset()`.
2. Ensure valid warmup memory.
3. **Re-observe between actions — mandatory**: after every action and
   before the next one, call `get_observation()` (EE pose + cameras +
   tactile in one render, each call is a full render) and reason from it;
   never chain two actions on the state carried in the previous action's
   response. `get_tactile_data()` additionally when contact is involved.
   Fetch only between actions — never mid-motion (the reflex loop already
   renders each step).
4. Prefer small, safe chunks: at most 0.05 m / 0.3 rad per `move_delta`
   call, and split large motions into waypoints.
5. **Grasp protocol**: first confirm the hand is empty — `get_tactile_data()`
   reads the far plane (~34 mm) with no contact. If it already reads contact
   (depth well below far plane), the gripper is holding something: do NOT
   open it as the first move; confirm with `get_observation()` what is held
   and act accordingly (the target may already be grasped, ready to insert
   or place). Only on a confirmed empty hand: approach with
   `goto_pose(..., z_approach=0.1)`, `close_gripper()`, verify the grasp
   with tactile (contact + depth in the grasp range, not empty-hand depth)
   and `get_observation()` before lifting or moving. Re-grasp if the fingers
   closed empty. **Before inserting or placing, confirm the held object is
   upright from the camera images** (`get_observation()`), NOT from
   `get_object_pose()` — its quaternion is always identity (non-privileged
   perception cannot read orientation). If it is clearly tilted in the
   image, straighten it with `rotate_gripper(...)` in small verified steps
   while high above the surface — do NOT correct tilt with world-frame
   `move_delta(dr*)`, and do NOT rotate near the target (the object will
   catch on contact).
6. End the episode with `check_success()` to confirm, then call
   `episode_video()` and report the returned `mp4` path in your summary so the
   operator can replay your run. The video lives on the server side (under
   `outputs/agent/`); report the path only, do **not** read files under it
   or the `index.json` frame labels.

## Safety rules

- Keep probes and actions small; never jump more than 0.05 m / 0.3 rad
  between consecutive targets.
- Never force against the environment: when contact is detected, stop
  pressing — read the tactile depth and back off if it keeps decreasing.
- Never command target Z below 0 (below the table).
- There is no `reset()` — you cannot start over. If an attempt fails, the
  scene stays as you left it: adapt in place (re-grasp, reposition, try a
  different approach) or report the failure honestly. Never loop on the
  same failing action hoping the scene will change.
- If a call raises (IK failure, connection error), inspect the error
  message: smaller steps, a different approach direction, or a reachable
  target usually fix it. Stop only after repeated failures.
- **cuRobo planning failures are usually deterministic, not transient.**
  If `goto_pose` / `move_delta` returns "Motion planning failed", do NOT
  retry the same target — it will fail again the same way. Change the
  plan instead:
  - split a large orientation change into steps (`rotate_gripper`, or
    small `move_delta(drx/dry/drz)`) instead of one big rotation;
  - move to an intermediate pose first, then descend (e.g. raise first,
    re-align in a clear pose, then approach);
  - change the approach direction or `z_approach`;
  - if the current tool orientation makes the target unreachable, first
    restore a known-good pose (vertical tool, high above the table).
  Only retry an identical target for truly small deltas, and at most twice.
  After two consecutive failures on the same goal, back off, observe, and
  re-plan rather than repeating the call.
- Treat `check_success()` as a final confirmation, not a steering signal.
