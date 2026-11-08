---
id: A26
name: strap-load-is-shear-difference
family: sense
summary: From the position=0.22 close, raising the hand reads 0.93 N per px of the two pads' shear difference, minus a 2.3 mm take-up times the gain you measure; near the target step 0.5 mm, not 2.
task_types: [force]
channels: [shear]
objects: the elastic strap, gripped by the tab at its top
evidence: Four seeds, one close, one 2 mm ramp — the slope was 0.928, 0.932, 0.936 and 0.947 N per px, the line held to 20.7 N with a 0.09 N residual, and both pads' displacements and coherence stayed flat across it. The pixel take-up was not constant (4.74 down to 3.73 px) but the take-up over the gel's gain was (2.27 to 2.31 mm of hand rise), which is why the gain is measured rather than carried.
---

## How

Ask `sample_grasp_pose("elastic_strap")` for the pose first, and approach it
straight down: the close is an opening, so the pose decides where the pads
land. A hand-built pose is four to five millimetres off — it closed at
25.60/24.94 mm where the task's own pose closes at 25.70/25.25, and the gel's
gain read 796 px/m against 1600.

Close by opening, not by depth: `close_gripper(position=0.22)` lands both pads
at 25.70/25.25 mm every run and frees the strap.

1. Take the two pads' shear difference at the close as zero, both axes.
2. Rise 2 mm, then 2 mm again. (second - first) / 2 mm is the gel's gain, in
   px of slip per millimetre the hand rises.
3. Climb in 2 mm steps until `0.93 * (slip - 2.3 mm * gain)` reads the target.

## Traps

- **The depth is not the load.** It reads the squeeze between the pads and
  does not move while the strap is pulled.
- **A dead pad is not a grasp.** If the two pads' displacements differ by more
  than about a third at the close, one field was dragged along with the jaw
  and its shear is that drag.
- **Never go down.** A descent and a second climb reads 1.6 px short of the
  same pose reached in one direction.
- **Do not jump, and do not creep.** A 5 mm step reads 0.9 px short of the
  same pose in 2 mm steps, and a half-millimetre correction costs another
  0.6 px. Both are most of the band.
- **Three raises that buy less than 0.5 N are not a weak pull.** They mean the
  pads are not on the tab, or the strap is still held by its fixture; a longer
  pull only measures the fixture. Open, re-approach and close at 0.22 again.
