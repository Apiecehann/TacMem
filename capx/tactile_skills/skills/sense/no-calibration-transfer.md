---
id: S22
name: no-calibration-transfer
family: sense
summary: Keep sensor identity, contact pose and action conditions with each raw observation;
  another configuration may respond differently.
task_types:
- classify
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original S22; no new experimental
  outcome is asserted.
---

## How

Record the raw image or depth together with the current command and robot state. Compare observations obtained under comparable conditions.

## Traps

Do not transfer numeric property boundaries or force conversion rules from a different sensor, object or contact pose.
