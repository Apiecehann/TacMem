---
id: S15
name: slip-pair-baseline
family: sense
summary: Compare available movement readings before and after the action; one reading is not a change.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
not_tasks:
- blind_pick_balls
channels: []
objects: any
evidence: Retained applicable rules from fixed source S15; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Save a public baseline before the action being judged and compare subsequent readings from the same pad. Do not call an API absent from the configured function list or label every deformation change as object slip.
