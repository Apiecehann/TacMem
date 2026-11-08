---
id: S12
name: can-close-depth-limit
family: sense
summary: 'Do not use the block''s force_threshold pair on a compliant can: 20.5 mm is a depth the servo cannot reach.'
task_types: []
tasks:
- tactile_memory_match
channels:
- depth
objects: rigid cans; on a compliant can the same contrast measured 0.002-0.016, at or under the line
evidence: Only the original close-range limitation is retained; S-dependent classification and evidence remain in the source snapshot.
---

## How

The block's force_threshold pair does not come here: 20.5 mm is a depth the
servo cannot reach on a compliant can, so that close fails outright rather
than stopping short ([[no-calibration-transfer]]).
