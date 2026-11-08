---
id: S21
name: close-lands-on-target
family: sense
summary: A commanded gripper target does not establish the corresponding sensor reading.
task_types:
- classify
channels: []
objects: any
evidence: Retained applicable rules from fixed source S21; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Keep a successful actuator command separate from measured contact evidence. A blocked closure may leave the fingers on an object; inspect only the observations available in this condition.
