---
id: R5
name: regrasp-after-slip
family: protocol
summary: Once it has slipped, or the gate keeps rejecting, re-grasp — carrying on spends the rest of the motion
  slipping.
task_types:
- insert
- manipulate
- screw
- wipe
channels:
- shear
- coherence
objects: any held object
evidence: Filtered from the fixed original skill R5; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Open, re-approach in a **single** call, close again, and verify before resuming:

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
