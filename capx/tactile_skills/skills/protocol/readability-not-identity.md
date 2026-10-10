---
id: R7
name: readability-not-identity
family: protocol
summary: Reading a difference is not the same as saying which one it is.
task_types: [classify]
channels: []
objects: any
evidence: A criterion can separate every pair of objects it was calibrated on and still have no way to place a new one, because the calibration was a gap between known objects rather than a scale.
---

## How

Decide which question the task is asking before choosing the criterion:

- **same or different** — a difference between two states is enough, and it is
  the robust form ([[differential-across-states]]);
- **which one is this** — an unknown object has to be placed against something
  measured in the same run, because there is no absolute scale to place it on.

When the task is matching, the answer comes from the comparison, not from a
threshold that names a class.

## Traps

- A clean gap measured between two known objects tempts you into reading the
  midpoint as a threshold that identifies an unknown one. It separates the two
  it was measured on; that is all it was ever shown to do.
- Discriminability is a property of the pair, not of the channel. A strong
  separation on one contrast says nothing about a third object outside the
  calibration ([[no-calibration-transfer]]).
- The confidence gate does not help here. It tells you whether a reading is
  real, not whether a real reading is interpretable
  ([[confidence-gate]]).
