---
id: A6
name: asymmetry-means-misaligned
family: act
summary: A pad depth difference means the part is tilted, not that the reading is noisy.
task_types: [insert]
channels: [depth]
objects: the insert tasks' pegs
evidence: The reflex condition for it treats |L_depth - R_depth| >= 3.0 mm as yaw misalignment, and the insert prompts name tactile asymmetry as the indicator to correct on.
---

## How

Read both depths, take the difference, and correct the angle rather than the
position:

```python
move_reflexive(dz=-0.004, stop_on={"asymmetry": 3.0})
```

The stop gives you `asymmetry_mm` in its digest, so the size of the correction
is in the same reading as the decision to make it.

## Traps

- Pushing harder on an asymmetric contact drives the high side further into the
  rim. The correction is angular.
- Correct the angle where there is room — high above the surface, in the tool
  frame — not next to the target, where the part catches on whatever it is
  about to enter ([[orientation-fix-high]]).
- **A depth gap means a tilt; a displacement gap does not.** Displacement is
  commonly one-sided on a perfectly good hold, so treating every asymmetry as
  misalignment rejects valid readings ([[grasp-verification]]).
