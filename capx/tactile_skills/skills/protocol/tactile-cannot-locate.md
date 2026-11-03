---
id: R8
name: tactile-cannot-locate
family: protocol
summary: Use allowed position queries and scene vision for approach; local fingertip
  images do not supply distant object coordinates.
task_types:
- classify
- insert
- manipulate
- screw
- wipe
not_tasks:
- blind_pick_balls
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original R8; no new experimental
  outcome is asserted.
---

## How

Approach using documented geometry and robot pose. Inspect raw fingertip signals only as observations of the current local interaction.

## Traps

In blind search the ball position remains hidden; do not infer an exact distant coordinate from an untouched pad.
