---
id: R6
name: cruise-height
family: protocol
summary: Travel at a cruise height and descend only at the target.
task_types: [insert, manipulate, wipe]
channels: []
objects: any
evidence: A sideways move while the object still rests on the surface topples it instead of moving it, which is why lifting clear is the first step of every transport.
copies:
  - univtac_skill/SKILL.md
---

## How

Split every motion into three: rise clear, travel, descend. Keep the travelled
leg well above the table, and make the descent the only slow part — small steps,
stopping on contact or on the depth ([[small-step-insertion]]).

## Traps

- Lifting the object clear is a separate action from cruising high. An object
  that has never left the surface is dragged by a lateral delta however high
  the waypoints afterwards are ([[lift-before-lateral]]).
- Keep each call inside the per-call limits and split longer moves into
  waypoints; a large single jump is rejected rather than truncated.
- The descent at the target is where a tilt should already have been corrected.
  Straightening the object next to the target catches it on whatever it is
  about to enter ([[orientation-fix-high]]).
