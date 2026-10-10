---
id: P1
name: world-frame-transport
family: place
summary: Carry a held object with world-frame translation deltas, never a pose command.
task_types: [classify, insert, manipulate]
channels: []
objects: any
evidence: Same-seed measurement while landing an object — 11-45° of tilt with a pose command against 0.2-0.3° with world-frame translation.
copies:
  - docs/univtac_api_reference.md §3.1 and §3.4
---

## How

Move the hand by the delta you want (`move_delta(dx=..., dy=..., dz=...)`); a
rigidly held object moves with it, and the wrist orientation is left alone.
Once the object is clear of the table, world deltas need no grasp-offset
arithmetic: the object moves exactly what the hand moves.

## Traps

- A pose command re-commands the wrist to the grasp quaternion. While the
  object is touching down in a light grip, that rotates it in place — the tilt
  shows up in the placement, not in the hand.
- Rotations default to the world frame, where an axis rotation swings the whole
  arm: 0.04 rad moves a held object about 2 cm at this reach. For an in-place
  reorientation use the tool frame, or `rotate_gripper` — and do it high above
  the surface, never next to the target.
- While the object still rests on the table, a sideways delta topples it. Lift
  ~3 cm first. See [[lift-before-lateral]].
- A world-delta loop only terminates where the API lets it: the 2 mm floor is
  the end of the loop, not a step to round a residual up to
  ([[small-step-insertion]]).
