---
id: S9
name: finger-position-grip
family: sense
summary: Choose position-controlled opening from known object width and the documented gripper range; a position command alone does not establish grasping.
task_types:
- classify
channels: []
objects: rigid objects only; a compliant one yields instead of pinning the fingers
evidence: Original finger-position skill includes the geometry-based position-control option; all sensor scale and threshold advice is removed.
---

## How

Use position commands and allowed geometry; compare the resulting robot state with the requested motion.

## Traps

Do not assume every object is rigid or that a position command guarantees a grasp.
