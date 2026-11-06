---
id: R9
name: grasp-verification
family: protocol
summary: Inspect raw images and depth from both fingertips after closing and during transport; a sent
  close command does not prove a stable grasp.
task_types:
- classify
- insert
- manipulate
- screw
- wipe
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original R9; no new experimental
  outcome is asserted.
---

## How

Collect left and right raw images and depth after closing. Observe changes when the robot lifts and carries the object, together with allowed robot state and scene vision.

## Traps

A missing image provides no evidence. A visible local imprint alone does not establish task completion.
