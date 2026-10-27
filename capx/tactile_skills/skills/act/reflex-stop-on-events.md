---
id: A1
name: reflex-stop-on-events
family: act
summary: Freeze the arm on the server's tactile event instead of polling between steps.
task_types: [classify, insert, manipulate, screw, wipe]
# blind_pick_balls serves seven primitives and no reflexive call, so the stop
# condition this skill names cannot be written on any of them.
not_tasks: [blind_pick_balls]
channels: [depth, shear, coincidence]
objects: any
evidence: The tactile stream updates every physics step while an LLM turn takes seconds; polling between steps reacts a step late and burns wall-clock. ~2 FPS in reflex mode (every step is rendered), so keep deltas ≤ 0.05 m.
copies:
  - univtac_skill/SKILL.md (Two-loop control)
  - docs/univtac_api_reference.md §3.11
  - docs/univtac_tactile_reflex_architecture.md
---

## How

Name the condition on the call; all named conditions are ORed, and an absent
one is off:

| `stop_on` | fires when | use it for |
|---|---|---|
| `contact=True` | a pad leaves the 34 mm far plane | first touch |
| `plateau={"mm": 0.5, "steps": 5}` | the press stops deepening (armed only at ≤ 27.5 mm) | seated, jammed, bottomed out |
| `hard_depth=20` | the gel reaches the hard stop | jam protection |
| `slip=30` | slip direction turns 30° (direction-aware) | the part starting to turn in the grip |
| `shear_spike=2` | shear jumps 2 px with coherence ≥ 0.7 | a sudden catch |
| `asymmetry=3` | the pads differ by 3 mm in depth | the grip gone crooked |
| `max_steps=50` | step count | a safety net for a motion that never ends |

## Traps

- `success=True` can mean "stopped on a condition", not "reached the target".
  Read `terminated_by` before believing a motion arrived anywhere.
- Read the digest, not just the flag: `min_depth_mm` is how hard the press
  got, `asymmetry_mm` which way it was lopsided, `shear_dir_deg` which way the
  surface pulled (a plug that seats and one that jams shear differently), and
  `events` the order things fired in.
- A triggered motion is not a failure and is not retried internally. Do not
  re-run the same contact at a smaller scale — back off and re-plan.
- It replaces a poll-between-steps loop for a one-shot descent. Keep the slow
  loop when each step needs an image or a decision the reflex cannot make.
