---
id: P3
name: seat-height-from-plate
family: place
summary: Seat the object's base on the support's surface — derive the stop height from the two poses, never from contact.
task_types: [classify, manipulate]
channels: []
objects: any
evidence: Once the object is clear of the table, world-frame deltas move it exactly, so the computed height needs no grasp-offset correction. The blocks, plates, cans and pads in these scenes all carry their pose origin at their base, so the number to command there is the support's surface itself.
---

## How

The target is the support's surface height, adjusted for where the object's own
pose origin sits:

- **origin at the base** (the blocks, the cans, the pads) — command the origin
  to the support's surface; the object's own extent never enters the number;
- **origin at the centre** — the support's surface plus half the object's
  extent along the approach axis.

When the *support's* origin is at its base too, its surface is its origin plus
its own extent. Both numbers come from the scene, not from contact.

## Traps

- **The convention decides the arithmetic, and getting it wrong is silent.**
  Half the extent is right for a centre origin and wrong for a base one, and
  the two differ by half the object — which shows up as a press or a drop
  rather than as a number that looks wrong.
- Pressing until resistance is how a placement becomes a push: the object or
  its support takes the extra travel. See [[descend-until-supported]].
- Stopping short leaves the object to drop the last few millimetres, which is
  enough to tip it or to roll it off-centre.
- Re-derive the number when the object or the support changes; it is a property
  of the scene, not of the method.
