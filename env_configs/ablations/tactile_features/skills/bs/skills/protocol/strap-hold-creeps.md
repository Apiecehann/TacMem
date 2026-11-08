---
id: R15
name: strap-hold-creeps
family: protocol
summary: A hold requires repeated commands that advance simulation time; waiting on an idle arm does not count.
task_types: []
tasks:
- tension_strap
channels: []
objects: the elastic strap
evidence: Retained applicable rules from fixed source R15; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Keep the commanded pose during a hold and count actual physics time until the requested duration is covered. Target force and duration are task requirements, not measured evidence that they were achieved. An incomplete hold does not earn partial completion.
