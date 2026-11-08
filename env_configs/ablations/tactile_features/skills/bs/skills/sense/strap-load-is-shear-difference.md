---
id: A26
name: strap-load-is-shear-difference
family: sense
summary: Grip the strap tab with a position=0.22 close; this condition has no calibrated tension conversion
  from this skill.
task_types:
- force
channels: []
objects: the elastic strap, gripped by the tab at its top
evidence: Retained applicable rules from fixed source A26; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Use the existing position-commanded grip and the episode's requested targets. Keep the measured hand height separate from measured tension: the strap stiffness changes with seed. Do not reuse an earlier seed's height as a Newton calibration or invent a conversion coefficient.
