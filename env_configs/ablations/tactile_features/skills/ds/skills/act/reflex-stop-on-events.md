---
id: A1
name: reflex-stop-on-events
family: act
summary: Freeze the arm on the server's tactile event instead of polling between steps.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
not_tasks:
- blind_pick_balls
channels:
- shear
objects: any
evidence: Filtered from the fixed original skill A1; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Name the condition on the call; all named conditions are ORed, and an absent
one is off:

## Traps

- `success=True` can mean "stopped on a condition", not "reached the target".
  Read `terminated_by` before believing a motion arrived anywhere.
- Read the digest, not just the flag: `min_depth_mm` is how hard the press
  got, `asymmetry_mm` which way it was lopsided, `shear_dir_deg` which way the
  surface pulled (a plug that seats and one that jams shear differently), and
  `events` the order things fired in.
- A triggered motion is not a failure and is not retried internally. Do not
  re-run the same contact at a smaller scale — back off and re-plan.
- It replaces a poll-between-steps loop for a one-shot descent. Keep the slow
  loop when each step needs an image or a decision the reflex cannot make.
