---
id: R10
name: channel-selection
family: protocol
summary: Use the two-pad shear difference for signed load or slide, anisotropy changes for can texture, and
  relief for ball texture where reported.
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
- shear
- anisotropy
objects: any
evidence: Retained applicable rules from fixed source R10; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Preserve the direction and baseline of marker_shear_dx/dy. marker_coherence describes motion agreement, not a contact detector. Compare marker_anisotropy at the same commanded grip states. height_roughness_mm is task-dependent; an absent value is unknown. No retained channel automatically establishes grip tightness or hardness.
