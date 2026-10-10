---
id: R5
name: regrasp-after-slip
family: protocol
summary: Once it has slipped, re-grasp — carrying on spends the rest of the motion slipping.
task_types: [insert, manipulate, screw]
channels: [shear, coherence]
objects: any held object
evidence: A close that never gripped carries nothing — every commanded twist is spent sliding the pads over the surface, nothing turns, and the squeeze climbs until the motion aborts on its force guard.
---

## How

Open, re-approach in a **single** call, close again, and verify before resuming:

- contact present on both pads and both depths clearly below the far plane
  ([[grasp-verification]]);
- then continue from where the motion stopped, not from the beginning.

## Traps

- Verify before deciding. A re-grasp costs a placement's worth of time and
  throws away a grasp that may have been fine, so the slip has to be real
  ([[slip-pair-baseline]]).
- The re-approach is where accuracy is lost, so it wants the one-call form
  ([[one-call-approach]]).
- Slip is also a measurement: it says the grip was too light for this object,
  and closing the same way again reproduces it ([[grip-force-by-mass]]).
- **A slip the reflex stopped on is not a failure.** The motion is never
  retried internally, so read `terminated_by` before treating the stop as an
  error ([[reflex-stop-on-events]]).
