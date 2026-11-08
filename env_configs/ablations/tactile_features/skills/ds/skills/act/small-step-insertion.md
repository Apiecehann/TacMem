---
id: A5
name: small-step-insertion
family: act
summary: Step in 2-5 mm and seat by depth, not force — a residual under 2 mm is the end of the loop, not a
  step to round up.
task_types:
- insert
- manipulate
- screw
not_tasks:
- blind_pick_balls
channels:
- force
objects: the insert tasks, the placements and the threaded bulb
evidence: Filtered from the fixed original skill A5; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Step by 2-5 mm per call and stop on what you feel rather than on a computed
target. Prefer a reflex stop over a hand-rolled loop:

```python
move_reflexive(dz=-0.005, stop_on={"plateau": {"mm": 0.5, "steps": 5}})
```

The press going flat over five steps is the seated-or-jammed signal, and it is
detected inside the motion rather than between steps
([[reflex-stop-on-events]]). Stop on the depth, or let that plateau stop you —
the extra travel past seating does not go nowhere: it compresses the object,
loads the fixture, or drives the fingers under the object until they push it
out of the grip.

The same rule governs setting an object down: keep stepping until the object's
own height stops falling, then stop ([[descend-until-supported]]).

## Traps
