---
id: A16
name: wrist-limit-segmentation
family: act
summary: Track signed wrist rotation, probes included. Before exceeding about 120 degrees, release, reverse that signed angle with rotate_gripper(feed_lead_m=0.0), then re-grasp; never substitute move_delta.
task_types:
- screw
channels: []
objects: light_bulb
evidence: Original A16 records completed segments near 120 degrees, refusals near 135 degrees, and a sign-accounting error. Only its rotation and recovery geometry is retained.
---

## How

Count all rotation commands since the last wind-back. Wind back only with the gripper open, using the opposite sign of the accumulated commanded angle and an explicit zero feed. Query the grasp pose again afterwards.

## Traps

Do not substitute a world-frame move_delta rotation for this wind-back. The 120 degree value is a conservative rule from the original bulb examples, not a universal joint limit. A native terminal refusal cannot be recovered by more commands.
