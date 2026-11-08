---
id: A14
name: mirrored-shear-slide
family: act
summary: Read the slide in mm as the shear x-channel's left minus right over 18 px/mm, signed — difference
  it across the move you judge.
task_types:
- screw
channels:
- shear
objects: light_bulb (gain calibrated on this rig)
evidence: Filtered from the fixed original skill A14; removed-channel evidence is withheld. No new execution
  evidence is claimed.
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
