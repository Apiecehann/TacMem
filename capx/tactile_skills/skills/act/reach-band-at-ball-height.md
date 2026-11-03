---
id: A25
name: reach-band-at-ball-height
family: act
summary: Verify low-pose arrival with get_ee_pose and action status; the original scene found approximate x reach 0.39–0.62 m.
task_types: []
tasks:
- blind_pick_balls
channels: []
objects: the twelve balls in the blind box
evidence: The existing reach-band skill records arm reachability in the ball scene; sensor readings are removed.
---

## How

Use get_ee_pose and action status to check arrival before proceeding. The original scene examples found a low-height x reach band approximately 0.39 to 0.62 m.

## Traps

That interval is scene-specific, not a guaranteed reach envelope or a ball location.
