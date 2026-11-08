---
id: S4
name: grip-force-by-mass
family: sense
summary: Pick the closing force from the mass — at ~28.5 the 15 mm block's two-pad shear difference between resting and lifted 3 cm reads 0.7-1.0 px heavy vs under 0.05 px light, split 0.2 px; by ~26 the shear stops.
task_types: []
tasks: [weight_classify]
channels: [shear]
objects: rigid objects; the two published figures are for the 15 mm block and for a can of 300 g or more
evidence: At 28.5 a 300 g can slips out — the markers sit at ~0.02 px and a 2 cm lift leaves it on the table; the shear signal also collapses by ~26 on the block.
copies:
  - docs/univtac_api_reference.md §3.4
---

## How

Take the grip force from what the object weighs, not from a single house
number. The band where the load is visible is narrow:

- a rigid object of a few grams — ~28.5, light enough to shear, firm enough
  that it does not slip out;
- a 300 g can or heavier — ~27.7.

A lift that leaves the object behind means the grip was too light. Close firmer
and lift again.

## Traps

- **Squeezing harder hides the weight.** Too firm a grip stops the gel shearing
  at all; the object is held better and reads lighter, which is the opposite of
  the intuition that a firmer grip is a safer measurement.
- A lift that leaves the object behind is not a failed measurement. The same
  close performed twice returns the same reading, and a close that read wrong
  comes out wrong again — only a firmer one changes the answer.
- The figure is object-specific. Re-derive it per object class rather than
  carrying 27.7 or 28.5 across ([[no-calibration-transfer]]).
