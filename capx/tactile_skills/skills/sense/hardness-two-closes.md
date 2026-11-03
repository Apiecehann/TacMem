---
id: S7
name: hardness-two-closes
family: sense
summary: For hardness, compare raw fingertip observations at two bounded position-controlled
  openings within the same grasp.
task_types: []
tasks:
- hardness_classify
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original S7; no new experimental
  outcome is asserted.
---

## How

Choose openings using known object width and the documented gripper range. Record the commanded opening, resulting robot state, and both raw snapshots at each opening.

## Traps

Different grip locations or moving samples can confound the comparison. Do not reuse a class threshold from another object.
