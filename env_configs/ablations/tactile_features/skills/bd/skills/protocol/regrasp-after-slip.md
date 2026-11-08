---
id: R5
name: regrasp-after-slip
family: protocol
summary: Once it has slipped, or the gate keeps rejecting, re-grasp — carrying on spends the rest of the motion slipping.
task_types: [insert, manipulate, screw, wipe]
channels: [shear, coherence]
objects: any held object
evidence: A close that never gripped carries nothing — every commanded twist is spent sliding the pads over the surface, nothing turns, and the squeeze climbs until the motion aborts on its force guard. The same is true of a grasp the tracker cannot read — on 2026-10-09 an evening run's left pad read 0.04 to 0.22 through three probes and the run had no lead at the end of them, and on 2026-10-10 a run's left pad read 0.26 to 0.32 through a fresh grasp and every block it drove had its reading rejected. Both kept the grasp.
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
- **A gate that keeps rejecting is a grasp to replace, not a move to repeat.**
  A pad reading under the gate block after block has not been given a surface to
  follow, and no further travel on that grasp buys one — re-driving the move
  spends descent and turn for a reading that was never coming
  ([[confidence-gate]]). On 2026-10-10 a run did exactly that and latched the
  servo with the bulb 2.67 mm in. Re-grasp instead, and verify the new one the
  usual way.
