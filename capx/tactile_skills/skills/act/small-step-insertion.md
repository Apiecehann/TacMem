---
id: A5
name: small-step-insertion
family: act
summary: Step in 2-5 mm and seat by depth, not force — a residual under 2 mm is the end of the loop, not a step to round up.
task_types: [insert, manipulate, screw]
# The balls task has no insertion and no support to seat against, and the 2 mm
# floor this skill turns on is already stated in move_delta's docstring.
not_tasks: [blind_pick_balls]
channels: [force, depth]
objects: the insert tasks, the placements and the threaded bulb
evidence: A delta below 2 mm on every axis cannot be executed and raises instead of moving nothing, which sets a hard lower bound on any loop that steps towards a target. Past the seated depth the press goes flat — the depth stops changing while the force keeps climbing. Only the loud side of that bound is obvious — on 2026-10-07 a live run rounded a residual under it up to the floor instead, and its alignment helper alternated ±2 mm for two hours and eleven minutes on a bulb and socket whose x/y already matched to under a millimetre.
copies:
  - univtac_skill/SKILL.md
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

- Asking for a smaller step to "close in" raises. The last millimetre is not
  reachable by commanding it — stop when the part is where you want it.
- **The floor has a silent side too, and that one hangs rather than raising.**
  Rounding a sub-2 mm residual up to the minimum looks like a way to still land
  the move: clamp the step, command it, subtract it, repeat. It cannot
  converge — the 2 mm step overshoots by the leftover, the next pass overshoots
  back the other way, and the loop alternates forever while every call returns
  success. A live run's world-frame alignment helper did exactly this, on a
  bulb and socket that already matched to under a millimetre, and spent two
  hours and eleven minutes going ±2 mm before it was killed. So test the
  residual against the floor *before* the loop, not inside it: under the floor
  there is nothing to command, and what is left over is the accuracy you have
  rather than a step still to take.
- A step that is too large does not merely overshoot: it turns a part that was
  about to seat into one that is jammed in at an angle.
- Force is not a substitute for position. `hard_depth` is a jam guard, not a
  seating criterion — it fires at the point where you already went too far
  ([[stall-means-jam]]).
- A seated part and a jammed one both read as a plateau. Separate them by
  whether the part reached the depth, not by how hard it feels.
- The target Z must stay above the table; a step that would go below it is
  rejected rather than clamped.
