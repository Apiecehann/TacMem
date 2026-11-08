---
id: A12
name: feed-must-match-lead
family: act
summary: Command the twist and the descent together — a thread's coupling is not a planned path.
task_types: [screw]
channels: []
objects: light_bulb
evidence: Feeds that do not match the thread are refused by the motion planner outright; a segment of 120° at a matched feed completed over 82 twists and twenty grasps with no refusal.
---

## How

Ask for a twist about the world z axis with the descent-per-turn supplied on
the call, rather than planning a rotation and a descent separately. In this
mode the step count is servo iterations, not planner sub-moves, and one
iteration carries the angle divided by that count plus its matched feed — the
call's own limits on the count are in `rotate_gripper`.

The coupling is the thread's own relation, which is why it cannot be expressed
as a trajectory: a planned path is resolved before contact exists, and the
contact is what decides how far the part may descend per degree.

## Traps

- Two motions — rotate, then descend — do not add up to a thread. Either the
  part is dragged out of its thread or the rotation stalls against it.
- A feed mismatch is rejected rather than corrected. See
  [[wrist-limit-segmentation]] for what a refusal costs.
