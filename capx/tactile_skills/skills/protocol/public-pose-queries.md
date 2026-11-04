---
id: R15
name: public-pose-queries
family: protocol
summary: >-
  Call `ee=get_ee_pose()` with no arguments; read `ee["position"]` (world XYZ,
  metres) and `ee["quaternion_wxyz"]` (WXYZ). This is wrist-flange GT, not
  object or fingertip position; use it to verify motion and compute wrist
  targets with permitted object geometry.
task_types: []
tasks:
- weight_classify
- grasp_classify
- hardness_classify
- tactile_memory_match
- bulb_tightening
- blind_pick_balls
channels: []
objects: Robot wrist and only the scene objects exposed by the current task API.
evidence: >-
  UniVTACTactileApi.get_ee_pose returns the /ee_pose dictionary;
  get_object_pose returns a three-item tuple from /object_pose.
  The combination task's reference-offset procedure is defined in
  grasp_offset_from_reference. These are interface contracts, not a claim
  of improved task success.
---

## How

Call the documented function by its exact name and access its dictionary keys:

```python
import numpy as np

ee = get_ee_pose()
wrist_position = np.asarray(ee["position"], dtype=float)
wrist_quaternion = np.asarray(ee["quaternion_wxyz"], dtype=float)
```

The position is the actual wrist-flange origin in world coordinates, in metres.
The quaternion order is WXYZ. Read the pose again after motion to check actual
arrival; a command returning does not guarantee that its target was reached.
A position passed to `goto_pose` is a wrist-flange target, not an object origin
or fingertip contact point.

Only when `get_object_pose` is present in the task's documented API, query an
allowed object role using its exact name:

```python
object_position, object_quaternion, object_extent = get_object_pose(
    object_name, return_bbox_extent=True
)
```

This returns three values, not a dictionary. Position and orientation come from
the simulator actor's world pose. The position is the authored actor origin:
its relation to the geometric centre depends on the asset. Extent is the full
bounding-box size in metres, not a half-extent; it can be unavailable. These
queries provide geometry, not the object's class, material or task verdict.

For combination matching, capture the reference grasp before moving the arm:

```python
reference_position, _, _ = get_object_pose("reference_object")
reference_grasp, grasp_quaternion = sample_grasp_pose("reference_object")
grasp_offset = np.asarray(reference_grasp) - np.asarray(reference_position)
candidate_position, _, _ = get_object_pose(candidate_name)
candidate_wrist_target = np.asarray(candidate_position) + grasp_offset
```

This transfer applies to the task's initially aligned, identically shaped cans.
Use the saved grasp quaternion and re-query each candidate's live pose. The
reference-specific helper must not be called for candidates or the placement
slot: without a task-specific rule, its first call caches the current wrist
pose rather than solving a grasp for a new object.

## Traps

- Keep the exact function name `get_ee_pose` and dictionary keys shown above.
  Do not unpack its return as the three-item object-pose tuple.
- The wrist, object origin and fingertip contact point are different points.
  Account for the applicable grasp geometry before forming a wrist target.
- In the ball retrieval task, only the robot's own pose is public. Do not call
  object-pose or grasp-pose functions that are absent from its API.
- A legacy diagnostic-only description refers to perception-based alignment.
  In these position-GT configurations, permitted object geometry and actual
  wrist pose may be combined; this does not expose hidden object attributes.
- Missing poses or failed queries are not zero coordinates or proof of arrival.

