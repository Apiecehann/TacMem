---
id: S2
name: gel-shear-weight
family: sense
summary: Weigh by the two pads' shear difference, baselined while the object still rests — depth never shows
  mass.
task_types:
- classify
channels:
- shear
objects: blocks of a few grams to a few hundred; the grip number changes with mass
evidence: Filtered from the fixed original skill S2; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

1. Close on the object **while it still rests on the table** and read — this is
   the baseline for this grasp.
2. Lift about 3 cm in ~1 cm steps, then read again while it hangs still.
3. Judge by the change in the *difference* of the two pads:
   `(left_hang - left_base) - (right_hang - right_base)` on the shear x
   component.

Light reads near zero (|·| < 0.05 px); heavy reads 0.7-1.0 px; the split is
around 0.2 px.

Depth is not an alternative here: it reports how tight the grip is, and the
servo lands on the same reading whatever the object weighs
([[depth-grip-scale]]). Neither is lift height on its own — that is the
follow-through comparison, not the load ([[lift-follow-height]]).

## Traps

- **The baseline must be unloaded.** It is valid only if the two pads differ
  within ~0.4 px; otherwise the grip already carries the object and a heavy
  block reads light. The close itself drags the object sideways as it seats,
  shearing the gel by about as much as a heavy load does.
- A baseline that is really loaded makes the whole measurement collapse, and it
  collapses toward zero — which reads like "this object is light" rather than
  like an error.
- Recover by opening, re-approaching in a **single** `goto_pose` call, and
  closing again; the split descent is what makes the close drag the object
  ([[one-call-approach]]). Subtracting the larger baseline instead does not
  work — the shear is already as far as this grip will take it, not merely
  offset, so the subtraction removes a constant that was never there.
- An object that does not follow the lift is held too lightly. Close firmer and
  lift again; the same command twice returns the same reading, so only a firmer
  close changes the answer.
- The destructive step goes last: a firm squeeze first destroys this reading
  ([[one-grasp-multi-property]]).
