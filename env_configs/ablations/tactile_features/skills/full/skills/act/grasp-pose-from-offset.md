---
id: A20
name: grasp-pose-from-offset
family: act
summary: For an object the hand was not above on the first call, build the grasp pose from a measured offset instead.
task_types: [classify, insert, manipulate, wipe]
# sample_grasp_pose is withdrawn on the balls line, so there is no measured
# offset to build a pose from.
not_tasks: [blind_pick_balls]
channels: []
objects: any object the pre-move did not leave the hand above
evidence: The API caches the hand-relative offset on the first sample_grasp_pose call, so for any other object it returns a pose at the hand's own location — above whatever was handled last.
---

## How

```python
grasp_pos = object_pos + offset   # offset = grasp_pos - object_pos
```

The hand's position relative to a held object's centre is the same for every
object, so reuse the offset from your first successful grasp.

## Traps

- **Only the object the pre-move anchored is reliable.** That is the one the
  hand starts directly above; for any other object the cached offset is the
  hand's current position and the returned pose lands somewhere else.
- Measure the offset off a real grasp, not off the object's nominal size: it is
  the difference between where the hand closed and where the object's origin
  sits, and both of those come from the run ([[no-absolute-without-reference]]).
