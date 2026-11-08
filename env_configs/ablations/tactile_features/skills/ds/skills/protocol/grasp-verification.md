---
id: R9
name: grasp-verification
family: protocol
summary: A completed close does not by itself prove a held object; the original basic-feedback grasp test is
  unavailable here.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
channels: []
objects: any
evidence: Retained applicable rules from fixed source R9; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Preserve the distinction between a successful motion command and an observed object hold. Use only allowed observations and permitted object pose queries; blind-box ball poses remain hidden. Do not invent a replacement grasp threshold or treat a quiet retained channel as a verified empty hand. Respect termination before any recovery.
