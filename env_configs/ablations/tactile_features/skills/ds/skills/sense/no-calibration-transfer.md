---
id: S22
name: no-calibration-transfer
family: sense
summary: A calibration does not transfer between object classes — re-measure per class.
task_types: [classify]
channels: []
objects: any pair of object classes with different widths or compliances
evidence: Both 0.10 and 0.06 are past a 4 cm can, so the two closes simply bottom out on it and a blind reading comes back looking like a confident answer instead of failing.
copies:
  - docs/univtac_api_reference.md §3.4
---

## How

Treat every threshold as belonging to the object it was measured on, and
re-derive it when the object changes. The one thing that does transfer is the
*shape* of the recipe — two closes bracketing the object, a difference across
states, a commanded grip.

## Traps

- **The failure does not announce itself.** A threshold applied past the end of
  its range returns a number in range; the difference collapses toward zero and
  reads as a measurement rather than as an error
  ([[confidence-gate]] has the same property on the marker side).
- Width is not the only axis. Compliance matters as much: a firm
  `force_threshold` that works on a rigid object is a depth the servo can never
  reach on a compliant one, so there the close fails outright
  ([[position-mode-for-yield]]).
- The cheap check is built in, and it does not depend on the object class —
  see [[differential-across-states]].
