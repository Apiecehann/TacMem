---
id: A14
name: mirrored-shear-slide
family: act
summary: Read the slide in mm as the shear x-channel's left minus right over 18 px/mm, signed — difference it across the move you judge; the field counts from the baseline.
task_types: [screw]
channels: [shear, coincidence]
objects: light_bulb (gain calibrated on this rig)
evidence: Small-slide calibration gave 17.6, 18.3 and 20.8 px/mm from slides of 0.11, 0.14 and 0.31 mm; a sideways probe that moved the part 0.5615 mm read −0.640 and −0.632 mm on two runs. All of it on `dx` — the same reader the environment's own expert uses. On 2026-10-10 a run read the field raw and compared two direction legs on it — both carried the hand's 5.4 mm of accumulated sink, six legs separated by less than the 0.15 mm gate, and the run died on the pad guard. The environment's own expert differences every reading — one before the twist, one after.
---

## How

```python
slide_mm = (r["left_marker_shear_dx"] - r["right_marker_shear_dx"]) / 18.0
```

Read the **difference**, never either pad. The sign says which way the part
moved relative to the fingertips — positive means it came down through them,
negative that it backed up. The size is that difference over 18 px/mm, the gain
*of the difference*: the mirror doubling is already inside it, so nothing is
halved again on the way.

**And read it as a change, not as a level.** Both fields are measured against
the reset-time no-contact baseline, so what a call returns is the episode's
total shear rather than what that one move did. Take a reading before the move
you are judging and one after it, and subtract: only the difference is the
slide that move made. The level is still the right reading when the question is
about the episode as a whole — the seat, which is a state — but every
comparison between two moves has to be made on the differences, or both moves
report the same accumulated total.

## Traps

- **The other axis is a live number, and it is not this one.** `dy` is a real
  measurement of a different direction, so a reader that takes it gets a
  plausible magnitude that never responds to the thread — the live agent run of
  2026-10-06 searched six leads on the `dy` difference and learned nothing. The
  gain, the sign convention and the environment's own reader are all on `dx`.
- **The field itself is the episode's total, not this move's.** It is counted
  from the reset-time no-contact baseline, so it carries the hand's own descent
  as well as the part's motion — every twist that sinks the hand adds to it
  whether or not the part moved, and two moves can read alike for a reason that
  has nothing to do with either. On 2026-10-10 a run compared two direction legs
  by the raw field, six legs under a 0.15 mm gate separated nothing, and the
  run's widening walked the pads to the server's 128 N guard.
- The sign is half the reading. `abs()` of the difference throws away which way
  the part went, and that is what tells a matched feed from an over-fed one
  ([[lead-by-overshoot]]).
- Valid only while both contact flags are up. A pad that is not touching
  reports the 34 mm depth sentinel and a shear that means nothing — and a
  missing reading arrives as `0.0`, so a one-sided failure quietly turns the
  difference into one pad reading alone instead of failing loudly.
- Trust the size at small slides and the sign alone at large ones. Those
  calibrations are small-slide ones; past a few tenths of a millimetre the pads
  read long on one run and short on the next.
- Multiply the per-block scatter by the number of blocks and it dominates: the
  per-block slide wobbles by 0.03-0.04 mm, so an insertion servoed block by
  block walks. See [[lead-by-overshoot]] for what to read instead.
