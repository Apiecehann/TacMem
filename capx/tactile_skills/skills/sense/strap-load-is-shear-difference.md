---
id: A26
name: strap-load-is-shear-difference
family: sense
summary: The load is the two pads' shear difference, about 1 N per px measured from the close — the depths do not move at all.
task_types: [force]
channels: [shear]
objects: the elastic strap, gripped by the tab at its top
evidence: Raised in 2 mm steps from the release point to 19.3 N of the task's own measured tension, `left_marker_shear_dx - right_marker_shear_dx` is a straight line at 1.031 N per px with -1.77 N of intercept, r^2 0.99992 and 0.05 N of residual — a tenth of the half-newton band this task is scored on. Over the same ramp both depths held at 27.79 / 27.06 mm and never moved, and the mean marker displacement was only half as linear because the right pad's own displacement saturates near 9.3 px. The 0.2 mm steps of a fine pass stay on the same line, with a worst residual of 0.25 N over 3.6-12.4 N.
---

## How

Pull, then read the difference of the two pads' shear along the pull:

```python
load = left_marker_shear_dx - right_marker_shear_dx
```

The pads are mounted mirrored, so the tab slipping down between them drags
their marker fields opposite ways. The difference is the load and it is
linear in it; either pad alone is half the signal and loses the other half to
whatever moved both of them.

The calibration belongs to the close.  Asked for 27.2 mm, the difference
reads newtons at about one for one — 0.98 N per px, linear to 0.23 N from 0
to 22 N — and its zero is whatever the pads had made of the gel at that
moment, 4.6 px of difference on the resting strap.

So take the zero from the close.  The fixture still holds the strap at that
moment, so the load there is nothing and the reading is the offset — a
number that belongs to the grasp, not to the calibration.  Measure every
later load from it; a shallower close moves it by more than a newton.

Close firmly, and check the strap followed.  The task frees it only when
both pads pass 27.7 mm *during* the close, and landings are not stable:
27.70/27.38 one run, 27.80/27.32 the next, same pose and same command.  The
27.80 one never released, and a pinned strap buys almost no tension when
pulled — 4.9 N after 12 mm where a free one gives 22 — while the tab slides
through the gel and reads as a large load that is not there.

What is worth measuring in-episode is the strap's stiffness in millimetres
per newton, because that is what the seed draws differently; the gel reads
the load itself, not the stretch.

Size a correction from the reading, not from the height. One 2 mm rise tells
you how many newtons this strap gives per millimetre this episode — it is
drawn per seed, so no number carried over from the last run survives — and
the rest of the pull can be computed from it.

## Traps

- **The depth is not the load.** It reads the squeeze between the pads, which
  the pull does not change: the tab is dragged along the gel, not pushed into
  it. Across the whole 0-19 N ramp both depths held constant to 0.02 mm.
- **Neither pad's own displacement can carry it.** The magnitude saturates on
  one pad (9.3 px on the right, against 11.5 and climbing on the left) while
  the load keeps rising, and the two pads do not saturate at the same time.
- **The difference is what cancels common motion.** A change in grip force or
  a drift of the whole hand moves both pads the same way and drops out;
  reading one pad would report it as load.
