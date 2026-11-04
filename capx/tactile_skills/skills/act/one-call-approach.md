---
id: A3
name: one-call-approach
family: act
summary: Approach the grasp pose in one goto_pose call with z_approach=0.1 m; avoid splitting it into two pose calls.
task_types:
- classify
- insert
- manipulate
- screw
- wipe
channels: []
objects: any
evidence: Original A3 reports approximately 1 mm approach error in its two-call example; this is an example, not a guaranteed bound.
---

## How

Pass the final grasp pose and approach offset to the same call. Use z_approach=0 for free-space motion when appropriate.

## Traps

An inaccurate approach can push the object sideways during closing; re-query its pose before another approach.
