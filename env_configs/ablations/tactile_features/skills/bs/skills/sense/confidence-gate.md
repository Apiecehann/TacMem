---
id: S19
name: confidence-gate
family: sense
summary: An unavailable measurement is unknown, not a vote; reading again must not replay the physical probe.
task_types:
- screw
channels: []
objects: any
evidence: Retained applicable rules from fixed source S19; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

If a probe was already executed, repeat only an available observation when it is useful. Re-driving the same physical block spends travel again and does not recover the missing measurement.
