---
id: P4
name: descend-until-supported
family: place
summary: When object pose is publicly available, lower in small steps until its own height stops falling; do
  not press beyond support.
task_types:
- classify
- insert
- manipulate
channels: []
objects: any
evidence: Retained applicable rules from fixed source P4; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Observe the object's actual height between downward steps and derive the support height from permitted object geometry. If no object pose API exists, this rule does not create one; use the task's public receiving geometry and keep support unverified until observed.
