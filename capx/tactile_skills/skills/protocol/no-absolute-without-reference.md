---
id: R4
name: no-absolute-without-reference
family: protocol
summary: Keep an observed baseline from this episode; raw pixel brightness or depth
  alone is not an absolute property scale.
task_types:
- classify
- screw
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original R4; no new experimental
  outcome is asserted.
---

## How

Record the available raw signals before loading and after a known action. If a task supplies a reference object, acquire its observations with the same procedure as the candidates.

## Traps

A baseline from another grasp can change with pose and illumination. A raw reading is not a calibrated force.
