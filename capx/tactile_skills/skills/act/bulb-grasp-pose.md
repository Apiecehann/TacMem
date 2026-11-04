---
id: A19
name: bulb-grasp-pose
family: act
summary: Re-query sample_grasp_pose("light_bulb") after each release; approach with its returned position and quaternion.
task_types: []
tasks:
- bulb_tightening
channels: []
objects: the light bulb
evidence: Original A19 describes the bulb-specific grasp helper and a 0.05 m approach.
---

## How

Use the returned grasp position and orientation together in goto_pose. The original example uses z_approach=0.05 m.

## Traps

Do not reuse a pose from before a release or build an arbitrary top-down quaternion.
