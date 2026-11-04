---
id: R1
name: differential-across-states
family: protocol
summary: Compare synchronized raw images and depth before and after a deliberate motion,
  preserving sensor identity and sampling time.
task_types:
- classify
- insert
- manipulate
- screw
- wipe
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original R1; no new experimental
  outcome is asserted.
---

## How

Save both fingertip snapshots before and after the motion under the same grasp. Keep the action and step associated with each image. Use only available RGB, RGB with markers, and full depth arrays.

## Traps

Do not compare different fingers, mix moving and resting samples, or fill an absent channel with zeros.
