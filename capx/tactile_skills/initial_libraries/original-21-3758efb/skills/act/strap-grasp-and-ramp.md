---
id: A30
name: strap-grasp-and-ramp
family: act
summary: Ask sample_grasp_pose for the strap's tab and close at 0.22 — measure the gain on the first two 2 mm rises; climb only upward, in steps of at most 2 mm, and three raises that change nothing mean re-grasp.
task_types: []
tasks: [tension_strap]
channels: [shear]
objects: the elastic strap's tab
evidence: >-
  The task's own grasp pose is the strap's base plus the tab's height, built by
  the server from the task's `base`. A hand-built pose is four to five
  millimetres off, and with an opening close that decides what the pads land
  on: 25.60/24.94 mm against the task pose's 25.70/25.25, and the gel's gain
  796 px/m against 1600. A close at 0.22 landed 25.70/25.25 on four straps,
  while a close without a position landed 27.31/26.99, 27.39/27.10,
  27.70/27.38 and 27.80/27.32 for one command. On the ramp, a 5 mm jump read
  0.9 px short of a 2 mm ramp at the same pose, and a descent followed by a
  second climb read 20.88 px where a monotone ramp read 22.52 — 1.6 N on a
  band of 1.0. Three rises that bought less than 0.5 N never left the fixture.
  Seed 5 (2026-11-05): the first two 2 mm rises gave 2.26 px/mm and the 4→8 mm
  chord 2.38, and a 1 mm descent read 15.84 px where the monotone 7 mm point
  interpolates to 16.06.
---

## How

**Grasp at the task's pose, not at a hand-built one.**

```python
pos, quat = sample_grasp_pose("elastic_strap")
goto_pose(pos, quat, z_approach=0.1)
close_gripper(position=0.22)
```

The server anchors this pose on the strap's base plus the tab's height, so it
is the pose the task's own grasp was measured at. The close is an opening, not
a depth target: 0.22 is the opening that landed the pads at 25.70/25.25 on four
straps.

**Climb only, in 2 mm steps.** Raise the hand by 2 mm and read after each step.
The first two rises are also the gain measurement
([[strap-load-is-shear-difference]]): (second − first) / 2 mm. The strap
stiffens as it stretches, so a later chord — 4→8 mm, say — overstates the gain.
Two millimetres is already about 2.4 N on a typical strap — wider than the
half-newton band — so the last steps want `goto_pose` at the exact height
rather than `move_delta`, whose minimum is 2 mm. Never descend to correct an
overshoot: the gel does not retrace its path, and a descent followed by a
second climb reads short of the same pose reached in one direction. Hold the
stage with [[strap-hold-creeps]].

**If the reading stops following the hand, re-grasp.** Three 2 mm raises that
buy less than 0.5 N mean the pads are not on the tab, or the strap is still
pinned to its fixture; a longer pull only measures the fixture. Open,
re-approach in one call ([[one-call-approach]]), close at 0.22 again.

## Traps

- **A hand-built grasp pose is four to five millimetres off.** It does not
  matter while the close is a depth servo; with an opening close it decides
  what the pads land on.
- **A close without a position lands somewhere new every run.** The four
  landings above span half a millimetre, and the reading belongs to where it
  landed.
- **A 5 mm jump is not the same pose as a 2 mm ramp.** It reads short; the gel
  answers a fast pull differently.
- **A downward correction costs more than the overshoot.** The field does not
  retrace, so the second climb comes back short.
- **Three pulls that change nothing are not a weak pull.** They are no grip, or
  a strap still held by its fixture.
