---
id: R1
name: differential-across-states
family: protocol
summary: Judge from two commanded states in one grasp — the difference cancels the drift the absolute carries.
task_types: [classify, force, insert, manipulate, screw, wipe]
channels: []
objects: any
evidence: Hardness reads two positions, texture two grips, weight two shear readings; in each case the absolute form drifts with the grasp and the difference does not. 34 mm is the no-contact far plane, and the loose close of a hardness pair is verified by reading exactly it.
---

## How

Pick two states that differ in the property's cause and in nothing else, then
subtract. Use the **same pad** in both readings so the mounting offset cancels
too. The subtraction turns "where did the close land" from a source of error
into a shared term that disappears:

- hardness — two commanded positions, difference of `min(L, R)` depth;
- texture — two commanded grips, difference of anisotropy;
- weight — two shear readings, resting against hanging, differenced across the
  pads ([[gel-shear-weight]]).

At the start of such a pair, the loose close must leave both pads at 34.0 mm.
That is not a nicety — it is the evidence that the two closes are actually
bracketing the object.

## Traps

- The two states must be **commanded**. If the first close lands wherever the
  servo put it, the difference inherits that landing, and the whole point was
  to cancel it ([[close-lands-on-target]]).
- If the loose close reads anything other than the far plane, the fingers were
  already squeezing, the two closes are bracketing nothing, and their
  difference means nothing. Go wider and close again rather than correcting the
  number afterwards. The same check guards the memory-match sequence, where the
  loose close at `position=0.50` must read the far plane before the firm close
  at 0.25 is allowed to count.
- The mounting offset between the pads cancels by reading the **same pad**
  twice, not by subtracting a constant: it is only constant to a few tenths of
  a millimetre, which is not good enough when the answer needs better than
  that.
- Re-landing the gripper between the two readings puts the difference back
  where you started — the states belong to one grasp.
- A differential cancels the shared term; it does not reduce noise in the term
  you want. If the signal is not there to begin with, the difference is a
  cleaner zero.
- Depth clamps near 20 mm and never reads below it, so the opposite end has its
  own version of this failure — a firm close past the clamp saturates and stops
  responding rather than failing loudly.
