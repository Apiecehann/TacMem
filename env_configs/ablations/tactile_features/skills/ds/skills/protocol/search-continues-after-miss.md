---
id: R14
name: search-continues-after-miss
family: protocol
summary: A missed grasp or a refused motion — the retract too — costs one candidate, not the episode; catch
  each, open, and try the next site.
task_types: []
tasks:
- blind_pick
- blind_pick_balls
channels: []
objects: the hidden block in the blind box
evidence: Filtered from the fixed original skill R14; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

The episode is a search over candidate columns, and one close tests only one
column. After a miss:

The same rule runs along a row in `blind_pick_balls`, where the refusals do
not come from a planner: the box has no planner and the hand is driven by a
differential IK, so a pose the arm cannot hold is refused by the arm itself —
the TCP comes back short (16 mm on the 2026-10-24 run) and the call reports
failure after the whole per-move step budget. Each ball the sweep stops at is
one candidate; a refusal at one of them costs that ball, not the run.

A motion call that fails — a planner rejection, or an adaptive close that
raises on air — is that candidate failing, not the episode. The try/except
must wrap the motion calls too, not only the close. **The retract is a motion
call too**: it is the last thing the loop does, so an unguarded refusal there
ends the run after all the other work has succeeded:

End only when the candidate list is exhausted or the run's own alarm has
fired. `check_success` is evaluated automatically at the end, so there is
nothing to gain by raising early.

## Traps
