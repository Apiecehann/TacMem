---
id: A15
name: lattice-pitch-budget
family: act
summary: Slides stay unambiguous within one lattice pitch, about 1 mm; past half of it the marker jumps.
task_types: [screw]
channels: [shear]
objects: light_bulb
evidence: False slides of 0.11, 0.08 and 0.08 mm appeared at a plausible size in runs where the lattice had unlocked; the practical budget is well under a pitch.
---

## How

Keep each twist short enough that the part cannot travel more than about half a
lattice pitch inside it. A few tens of degrees is the working unit: at the
longest admissible leads a 30° twist already slides most of a millimetre.

Absolute readings are only good to a few tenths of a millimetre before they
degrade; beyond that the reading still gives a reliable *sign* but not a size.

## Traps

- An unlocked lattice reports an unrelated number at the size a real reading
  would have, so no magnitude test can catch it — the budget is enforced by the
  coherence gate, not by a size check ([[confidence-gate]]).
- A third of a turn at a long lead is several times the budget, so the segment
  limit ([[wrist-limit-segmentation]]) is not the only reason to keep twists
  short.
