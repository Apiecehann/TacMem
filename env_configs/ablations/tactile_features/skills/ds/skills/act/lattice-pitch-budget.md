---
id: A15
name: lattice-pitch-budget
family: act
summary: A grasp's total slide stays readable to a few tenths of a millimetre; past that a reading keeps a sign, not a size, and never says so.
task_types: [screw]
channels: [shear]
objects: light_bulb
evidence: False slides of 0.11, 0.08 and 0.08 mm appeared at a plausible size in runs where the lattice had unlocked; the practical budget is well under a pitch. On 2026-10-10 a grasp walked it in its first block — 0.55 mm of slide, a 30° block at 11 mm a turn over a thread carried at 4.36 — and the four blocks after it (0.37, 0.28 and 0.18 mm of slide) read wreckage at 0.94 to 0.98 coherence — the budget is spent by the grasp, and the gate does not always catch the result.
---

## How

Keep each twist short enough that the part cannot travel more than about half a
lattice pitch inside it. A few tens of degrees is the working unit: at the
longest admissible leads a 30° twist already slides most of a millimetre.

**The budget is the grasp's, not the block's.** The field is counted from the
reset-time baseline, so what walks the lattice is everything the grasp has slid
by that point — every over-fed block it has already turned, whether or not that
block's reading was usable. A grasp can therefore be spent by its first block,
and nothing about the blocks after it is trustworthy once it is. Budget the
first over-fed block for the whole range and keep the later ones inside what is
left ([[lead-by-overshoot]]).

Absolute readings are only good to a few tenths of a millimetre before they
degrade; beyond that the reading still gives a reliable *sign* but not a size.

## Traps

- An unlocked lattice reports an unrelated number at the size a real reading
  would have, so no magnitude test can catch it, and the coherence gate does not
  catch it either: on 2026-10-10 a grasp whose first block slid 0.55 mm read
  0.94 to 0.98 coherence on every block after it, while all five of its readings
  sat at the feed. Nothing downstream detects what the budget was meant to
  prevent, so the budget is not something to spend and then check — it is the
  only thing standing between the block and a wrecked number
  ([[confidence-gate]]).
- A third of a turn at a long lead is several times the budget, so the segment
  limit ([[wrist-limit-segmentation]]) is not the only reason to keep twists
  short.
