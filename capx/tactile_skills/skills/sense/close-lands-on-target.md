---
id: S21
name: close-lands-on-target
family: sense
summary: Read back the depth a close landed on — a servoed close hits its target unless the grasp has wedged.
task_types: [classify]
channels: [depth]
objects: any
evidence: A close with an explicit force_threshold is servoed — after first touch the fingers micro-step in both directions until every pad sits inside the target ±0.15 mm — and the released depth comes back at the target with a spread of ±0.05 mm (calibration 2026-09-10, 3 reps × target 27.70 and 27.85 mm × 300 g and 650 g cans). It gives up rather than converging on an asymmetric or wedged grasp, which is the only case the reading lands off.
---

## How

Read `min(left_depth_mm, right_depth_mm)` from the close that just happened and
compare it with the `force_threshold` you asked for. Those two numbers matching
is the normal case, not a lucky one.

They disagreeing is a statement about the **grasp**, not about the servo: the
fingers were meeting the object unevenly or wedged, so the loop ran out of its
fine-step budget instead of converging. The recovery is to re-grasp, and it is
the same recovery as any other grip that never took
([[regrasp-after-slip]], [[grasp-verification]]).

## Traps

- **Do not "correct" a missed close by re-closing at `target - (realized -
  target)`.** That arithmetic modelled the older, unseroed landing, where the
  fingers took one coarse step and overshot by up to several millimetres. It no
  longer describes a close that was given an explicit threshold, and re-closing
  at a shifted target only pushes a wedged grasp further in.
- The exception is a close with **no** `force_threshold`: it is not servoed,
  lands near the task's default, and does vary from one close to the next — so
  there the realized depth is worth reading back, and there is no target to
  compare it against.
- Compare the two pads through `min(left, right)` rather than directly — the
  mounting offset between them is larger than any landing error
  ([[depth-grip-scale]]).
- Position-mode closes are pure kinematics and land wherever they are told, so
  this reading has nothing to say about them ([[position-mode-for-yield]]).
