---
id: S15
name: slip-pair-baseline
family: sense
summary: Call detect_slip once before the motion you judge — its first call only sets the reference.
task_types: [classify, insert, manipulate, screw, wipe]
# Its summary repeats what detect_slip's own docstring already states.
not_tasks: [blind_pick_balls]
channels: [coherence]
objects: any
evidence: A steady grasp does not re-report even one that slipped earlier; coherence is what separates uniform shear from compression and tracking loss.
copies:
  - docs/univtac_api_reference.md §3.10
  - univtac_skill/SKILL.md
---

## How

Call it once **before** the motion you want judged — the first call returns
False by construction and the motion before that call is never evaluated. After
that, each call compares against the previous one and updates the reference.

## Traps

- The displacement it reads is measured since the reset's no-contact baseline,
  so it stays high after a real jam. Only the increment between two calls means
  anything ([[channel-selection]]).
- **It does not detect that an object has reached the table.** Slow pressing
  compresses the gel rather than shearing it, so the growth stays under
  threshold and the loop keeps descending — the fingers then wedge under the
  object and push it out of the grip. Watch the object's own height instead
  ([[descend-until-supported]]).
- A large reading is not automatically a jam: the friction of a plug passing a
  socket's lip shears the markers hard and then decays. See
  [[stall-means-jam]] for the tiers.
