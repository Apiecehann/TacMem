---
id: S8
name: position-mode-for-yield
family: sense
summary: Measure yield with adaptive off — with the servo on a soft object reads firmer than a hard one.
task_types: [classify]
channels: [depth]
objects: compliant objects, whose jaws bottom out at ~26.5 mm
evidence: A firmer force_threshold on a compliant object is a target the servo can never reach, so with adaptation on it keeps pushing, the jaws bottom out, and the sign of the reading reverses.
copies:
  - docs/univtac_api_reference.md §3.4
---

## How

```python
close_gripper(position=loose, adaptive=False); d1 = min(left, right)
close_gripper(position=firm,  adaptive=False); d2 = min(left, right)
yield_ = d1 - d2
```

A commanded travel stops where it is told and lets the object yield, which is
exactly the quantity being measured. See [[hardness-two-closes]] for picking
the two positions.

## Traps

- **Adaptation on is not a stricter version of this, it is the opposite
  measurement.** The servo keeps pushing until the jaws bottom out, the gel
  then reads a firm jam, and the soft object ranks as the firmest in the set.
- A compliant object cannot be squeezed by `force_threshold` at all: a firm
  value is a depth the servo can never reach on it, so the close **fails
  outright** rather than stopping short.
- A commanded close is pure kinematics, so the same command from the same grasp
  pose returns the same depths — a close that read wrong comes out wrong again.
  Change where you grasp, not how many times you close.
