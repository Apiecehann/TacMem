---
id: A25
name: reach-band-at-ball-height
family: act
summary: For blind-box balls, work around x=0.39 to 0.62 at pad-centre z=0.050 m; read achieved wrist pose
  after a refused descent.
task_types: []
tasks:
- blind_pick_balls
channels: []
objects: the twelve balls in the blind box
evidence: Retained applicable rules from fixed source A25; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

The arm may stop above a requested site or shift horizontally. Use get_ee_pose to check where it actually stopped and the documented wrist-to-pad transform. A refusal is not proof that a site contains no ball. Preserve the public row geometry and existing workspace limits.
