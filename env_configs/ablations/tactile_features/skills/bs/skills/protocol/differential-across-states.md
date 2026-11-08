---
id: R1
name: differential-across-states
family: protocol
summary: Compare available responses at two commanded states in the same grasp; preserve the same pad and probing
  protocol.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
channels: []
objects: any
evidence: Retained applicable rules from fixed source R1; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

For hardness compare min(left_depth_mm, right_depth_mm) across the prescribed positions. For texture compare marker_anisotropy across those same grips. Verify a loose reading at the far plane before the firm reading when that protocol requires it. Keep measurements from the same pad and grasp; a missing property does not invalidate the others.
