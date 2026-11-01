---
id: A26
name: strap-load-is-shear-difference
family: sense
summary: The load is the two pads' shear difference, 0.93 N per px at a position=0.22 close — the depths do not move at all.
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

Close by opening, not by depth.  `close_gripper(position=0.22)` lands the
pads at 25.70 and 25.25 mm every time — three closes in a row, to a hundredth
of a millimetre — where the same depth command lands somewhere new each
time: 27.31/26.99, 27.39/27.10, 27.70/27.38, 27.80/27.32.  One of those
dragged a pad's whole marker field off its lattice and never let go.  The
opening frees the strap as well; 0.18 crushes to the sensor floor and
0.10-0.14 do not fit the tab at all.

At that grip the load is 0.93 N per px of the difference measured from the
close, above the first 4.3 px.  The take-up runs 4.1 to 4.5 px from strap to
strap and the slope does not: 0.932 on one and 0.939 on another, so aim with
the middle and both land inside the band.  The first two millimetres of pull
carry nothing, so a pull that buys no reading has not failed, and a line
through the origin is the wrong model (0.63 N/px, 2.9 N of residual).
Measure from the close and keep climbing: the field does not retrace its
path, so a descent and a fresh zero re-zeroes against a state that is not
the close (0.90 px on a 2026-11-01 run, 0.85 N, and its second stage landed
at 19.070 N).

What is worth measuring in-episode is the strap's stiffness in millimetres
per newton, because that is what the seed draws differently; the gel reads
the load itself, not the stretch.

Size a correction from the reading, not from the height. One 2 mm rise tells
you how many newtons this strap gives per millimetre this episode — it is
drawn per seed, so no number carried over from the last run survives — and
the rest of the pull can be computed from it.

## Traps

- **A dead pad reads as load.** A close can drag one pad's whole marker
  field along with the jaw; that pad then reports the drag. Its displacement
  is several times the other's — 49 px against 8.7 on a 2026-10-31 run — its
  coherence sits near zero, and it never recovers during the episode. The
  difference of the two is then the drag, not the tab: that run's gauge
  finished at 18.4 N against a strap carrying 15.383 N. Closing again in the
  same place does not mend it — a 2026-10-31 run tried five times at one pose
  and depth and got the same 5.6-6.4 ratio every time. A healthy pair reads
  within about a third of each other.
- **The depth is not the load.** It reads the squeeze between the pads, which
  the pull does not change: the tab is dragged along the gel, not pushed into
  it. Across the whole 0-19 N ramp both depths held constant to 0.02 mm.
- **Neither pad's own displacement can carry it.** The magnitude saturates on
  one pad (9.3 px on the right, against 11.5 and climbing on the left) while
  the load keeps rising, and the two pads do not saturate at the same time.
- **The difference is what cancels common motion.** A change in grip force or
  a drift of the whole hand moves both pads the same way and drops out;
  reading one pad would report it as load.
