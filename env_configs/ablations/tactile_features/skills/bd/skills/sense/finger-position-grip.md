---
id: S9
name: finger-position-grip
family: sense
summary: With a rigid object, grip by finger separation just above its width, not by depth.
task_types: [classify]
channels: [depth]
objects: rigid objects only; a compliant one yields instead of pinning the fingers
evidence: A rigid object holds the fingers off the gel, so a depth target is one the pads can never reach — position commands the separation directly and lands where it is told.
---

## How

`close_gripper(position=p)`, with `p` a fraction of the gripper's travel
(0.0 fully closed, 1.0 fully open) chosen just above the object's width. The
lightest grip that still touches is the interesting one — it is where a heavy
object slips out of the fingers and a light one does not, which is the contrast
the weight recipe needs.

## Traps

- Position and adaptation are alternatives, not layers. With `position` set and
  the adaptive close still on, the fingers stop at whichever comes first, so
  the travel you commanded is not the travel you get.
- This is not the way to measure yield. A commanded close that is meant to
  measure an object's compliance wants `adaptive=False` as well, and a
  different pair of positions — see [[position-mode-for-yield]].
- The number is object-specific: it is derived from the object's width, so it
  moves with the object ([[no-calibration-transfer]]).
