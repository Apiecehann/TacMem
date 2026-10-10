---
id: A4
name: lateral-only-align
family: act
summary: Align to a hole laterally only — probing in depth wedges the part on the rim.
task_types: [insert]
channels: [force, depth]
objects: the insert tasks' pegs and tilted holes or slots
evidence: A lateral probe of ~3 mm and a pose read-back separates the two states cleanly — seated, the pose barely moves (0.3-0.8 mm); caught on the rim, the part comes off by 45-52 mm.
copies:
  - docs/univtac_api_reference.md §3.14
---

## How

Make the correction in X and Y, and take the tilt correction as an angle rather
than as pressure. The lateral probe is also a measurement: push ~3 mm sideways
and read the pose again.

- the pose barely changed — the part is seated;
- the part moved tens of millimetres — it was caught on the rim; pull back and
  retry.

## Traps

- Pushing down to "find" the hole does not find it. A misaligned part presses
  its edge against the rim, the gel reads contact, and the extra travel goes
  into the jam ([[stall-means-jam]]).
- Keep the probe small. It is a diagnostic, and a large one converts a
  near-seated part into a dislodged one.
- The tilt that caused the misalignment is read from the two pads' depth
  difference, and it wants an angular correction rather than a lateral one
  ([[asymmetry-means-misaligned]]).
