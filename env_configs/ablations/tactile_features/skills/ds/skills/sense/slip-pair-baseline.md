---
id: S15
name: slip-pair-baseline
family: sense
summary: Call detect_slip once before the motion you judge — its first call only sets the reference.
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
- coherence
objects: any
evidence: Filtered from the fixed original skill S15; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Call it once **before** the motion you want judged — the first call returns
False by construction and the motion before that call is never evaluated. After
that, each call compares against the previous one and updates the reference.

## Traps
