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
- depth
- coincidence
objects: any
evidence: Filtered from the fixed original skill A1; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Name the condition on the call; all named conditions are ORed, and an absent
one is off:

## Traps
