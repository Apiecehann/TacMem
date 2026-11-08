---
id: S13
name: shear-vs-displacement
family: sense
summary: marker_displacement_px indicates deformation magnitude and does not retain direction.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
channels:
- displacement
objects: any
evidence: Retained applicable rules from fixed source S13; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Compare movement magnitude across actual actions, using the same pad and baseline. It cannot by itself provide a signed relative slide or a calibrated load. Do not treat the unavailable signed measurement as zero.
