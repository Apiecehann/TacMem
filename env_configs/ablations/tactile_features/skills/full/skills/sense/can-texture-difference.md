---
id: S12
name: can-texture-difference
family: sense
summary: For the cans, texture is the aniso difference between the loose and firm close, split 0.02.
task_types: []
tasks: [tactile_memory_match]
channels: [anisotropy, depth]
objects: rigid cans; on a compliant can the same contrast measured 0.002-0.016, at or under the line
evidence: The change is negative on every can measured, about -0.06 for a rough one against -0.017 to -0.033 for a smooth one; two cans of the same texture agree to ~0.007 while a rough and a smooth one sit 0.025-0.038 apart.
---

## How

The two closes the hardness read already makes — `position=0.50` (loose, reads
the 34 mm far plane) then `position=0.25` (firm), both with `adaptive=False` —
double as the texture read, so it costs no extra close. Read the anisotropy at
each and subtract the loose one from the firm one.

What carries the texture is the difference **between two cans**, not the value
of either. Treat ~0.02 as the line between "same" and "different".

## Traps

- **Rigidity is a precondition.** On a compliant can the same contrast came out
  at or under the line, so a texture call made there is a guess wearing the
  clothes of a reading.
- The block's force_threshold pair does not come here: 20.5 mm is a depth the
  servo cannot reach on a compliant can, so that close fails outright rather
  than stopping short ([[no-calibration-transfer]]).
- A single anisotropy reading drifts with where the grasp landed; the
  difference across the two closes does not, which is the whole reason the read
  is built this way ([[differential-across-states]]).
