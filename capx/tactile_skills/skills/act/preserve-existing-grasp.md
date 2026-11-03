---
id: A9
name: preserve-existing-grasp
family: act
summary: Preserve a held grasp unless the task, visible transport failure or reach requires another; re-query geometry after release.
task_types:
- manipulate
- insert
- screw
- wipe
channels: []
objects: any
evidence: Original A9 notes that every unnecessary release adds another approach and risks moving the object.
---

## How

Use ordinary images, allowed poses and action status to decide whether a new grasp is needed.

## Traps

Releasing changes the object pose. Re-query geometry before approaching again; follow the task release requirements.
