---
id: A19
name: bulb-grasp-pose
family: act
summary: Ask sample_grasp_pose for the bulb's grasp pose after every re-grasp — it is rebuilt per call.
task_types: []
tasks: [bulb_tightening]
channels: []
objects: the light bulb
evidence: This task has a ground-truth grasp rule, so the pose comes from the bulb's own pose in the simulator, not from where the wrist happens to be. It is rebuilt on every call, so it tracks the bulb after a release.
---

## How

```python
pos, quat = sample_grasp_pose("light_bulb")
goto_pose(pos, quat, z_approach=0.05)
```

Approach straight down onto the pose as returned.

## Traps

- **Ask again after the bulb has been released and caught again.** The pose is
  rebuilt per call; a pose kept from before a re-grasp is the one that goes
  stale ([[regrasp-after-slip]]).
- **Do not construct the quaternion yourself.** The wrist orientation comes
  back as the task's own approach has it; a built top-down quaternion puts the
  GelSight wrist near a singularity and makes the planner refuse.
- The general warning about the cached grasp offset does not apply on this
  task — that is why this skill names the bulb rather than following
  [[grasp-pose-from-offset]].
