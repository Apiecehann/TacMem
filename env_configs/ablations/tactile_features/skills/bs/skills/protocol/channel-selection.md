---
id: R10
name: channel-selection
family: protocol
summary: Use depth for grip and two-close yield, anisotropy for can texture, and height relief for ball texture;
  movement magnitude alone is not calibrated load.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
not_tasks:
- blind_pick_balls
channels:
- anisotropy
objects: any
evidence: Retained applicable rules from fixed source R10; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Use depth changes between the task-specific commanded grips for yield, and anisotropy differences at the same grips for can texture. Where provided, height_roughness_mm describes surface relief. marker_displacement_px measures deformation magnitude, not its direction. Keep missing property estimates unknown; do not infer a force conversion from magnitude.
