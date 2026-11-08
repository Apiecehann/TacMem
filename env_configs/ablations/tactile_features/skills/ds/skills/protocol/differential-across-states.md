---
id: R1
name: differential-across-states
family: protocol
summary: Compare retained responses across two commanded states of the same grasp, using the same pad in both.
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

For texture compare marker_anisotropy at the prescribed grip states. For load use the two-pad signed difference with a resting baseline. A repeated state from a different grasp is not the same baseline. If the original validity check is unavailable, preserve that uncertainty instead of claiming the probe was verified.
