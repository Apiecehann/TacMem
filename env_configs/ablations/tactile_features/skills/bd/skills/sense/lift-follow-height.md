---
id: S5
name: lift-follow-height
family: sense
summary: With a reference object, lift ~2 cm at 27.7 and compare how far each follows the hand.
task_types: []
tasks: [weight_classify]
channels: []
objects: any object the weight grip can hold; the comparison is only valid between objects measured in the same run
evidence: At this grip both objects stay near the hand, so held-versus-dropped carries no signal — the usable quantity is the gap between them.
---

## How

Close at `force_threshold=27.7` and lift ~2 cm in small steps, then read how
far each object rose. Compare the two objects against each other, within the
run.

## Traps

- Judge the comparison **within the run**, never against a fixed number of
  millimetres. How far an object follows depends on where the close landed, so
  an absolute threshold measured on one day does not hold on the next.
- Do not score held-versus-dropped. At this grip both stay near the hand, and
  the difference between them is the whole measurement.
- **This reads the consequence of the load, not the load.** The weight is
  already in the gel as shear; when a shear difference is available, use it
  ([[gel-shear-weight]]) and treat this as the fallback for when there is no
  reference object to hang.
