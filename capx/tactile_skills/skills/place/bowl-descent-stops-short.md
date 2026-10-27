---
id: P8
name: bowl-descent-stops-short
family: place
summary: Over the bowl the ball hangs 34 mm below the pads, above a mouth 150 mm across, so opening the gripper there, or wherever a refused descent stopped, leaves it inside.
task_types: []
tasks: [blind_pick_balls]
channels: [depth]
objects: a ball carried back from the box to the bowl
evidence: Two runs, each read off the task's own physical_trace.jsonl, which carries the TCP, the two gel depths and loads, and per ball its lowest vertex and its distance to the TCP. Seed 3 (gpt-6-astra, 2026-10-26) commanded the hand from the cruise height down to a pad centre of 0.042 over the bowl's centre and stopped at 0.0482, the ball's lowest vertex at 0.0144 against a floor at 0.016 — 6.7 mm high and 7.8 mm to the side of the commanded point — while the depths (27.25 / 26.25 mm) and the loads (33 / 28 N) stayed exactly where the carry had them. Seed 1 (2026-10-25) brought two deliveries commanded to 0.088, both stopped at 0.1002 and 0.1001 with the ball's underside at 0.0649 and 0.0657 — 49 mm above the floor — and sat there for 44 and 43 control steps; a 6 mm wider grip and a fivefold load difference (128.5/120.7 N against 26.9/27.8 N) changed nothing. Seed 3 again on 2026-10-27 held the pads over the bowl's centre at 0.0925 falling to 0.0906 — drifting about 11 mm sideways while it did — with the ball's lowest vertex at 0.0557 down to 0.0549 and its centroid a steady 0.018 from the TCP, then opened the gripper from a pad centre of 0.0703 with the pads 12-15 mm off the bowl's centre, and the ball came to rest with its lowest vertex at 0.0164, on the bowl's floor. Seed 4 on the same day released with no descent after it at all — pads held at 0.1122-0.1124, the ball's lowest vertex 0.0777-0.0778 (34.6 mm under the pad centre, 27 mm above the rim's top), 4.3 mm off the bowl's centre — and the ball fell 61 mm to the same floor, its lowest vertex 0.0164 for the last 30 rows of the trace. The control ran the same reset, the same xy, the same hand orientation and the same cruise height with an empty hand, which descends to a pad centre of 0.045 in even 18 mm steps, at the bowl and 80 mm further out, with no stall. The ball's underside rode 33.8-35.3 mm below the pad centre in all four deliveries. The bowl is the same asset at every seed and matches the task's constants (BOWL_CENTER [0.49, -0.34], BOWL_RADIUS 0.075, FLOOR_Z 0.016, BALL_RADIUS 0.030), measured as mouth centre (0.490, -0.340), inner radius 75 mm, rim top 0.051, floor 0.016.
copies:
---

## How

The pads put the ball in; the descent does not. The ball's underside rides
34-35 mm below the pad centre, so at the height the hand already stands over
the bowl — pad centre 0.09 to 0.11 — that underside is at 0.055 to 0.078,
above the rim's top at 0.051 and over a mouth 150 mm across against a 60 mm
ball. Opening the gripper there drops the ball onto the floor plate with
nothing to clear; a descent only changes how far it falls. Seed 4 on
2026-10-27 did exactly that without spending a descent at all: it stopped at a
pad centre of 0.1123 — the ball's underside at 0.078, 27 mm above the rim —
and opened 4 mm off the bowl's centre, and the ball fell 62 mm to rest on the
bowl's floor. Seed 3 did it from lower down, 0.0703, after hanging there for
24.5 s, and the ball landed on the same floor.

When the hand does go down, one of the two stops measured at the bowl has a
mechanism:

| | commanded pad centre | where the pads stopped | the ball's underside | what met first |
|---|---|---|---|---|
| seed 3, 2026-10-26 | 0.042 | 0.0482 | 0.0144 | the bowl's floor at 0.016 |
| seed 1, 2026-10-25 | 0.088 | 0.1002 / 0.1001 | 0.0649 / 0.0657 | unidentified, 49 mm above the floor |

The floor one follows from the geometry: the ball's underside rides 34 mm below
the pad centre and the floor is at 0.016, so the pads bottom out near 0.050 and
any lower command is refused a few millimetres short. At seed 3 that shortfall
was 6.7 mm vertically and 7.8 mm sideways, with the depths at 27.25 / 26.25 mm
and the loads at 33 / 28 N, unchanged from the carry: nothing left the grip.
The bowl is the same asset at every seed — mouth centre (0.490, -0.340), inner
radius 75 mm, rim top 0.051, floor 0.016 — and the ball is 60 mm across.

Either stop is a usable release. The pads end inside the mouth: at seed 3 the
ball's underside is already resting on the floor, at seed 1 it is 14 mm above
the rim's top and falls through. A program that turns back at the refusal
carries the ball home instead, and that does lose it. The seed-1 stop is not
the arm running out of room and not the bowl in the way: the same descent from
the same cruise height with an empty hand runs down to a pad centre of 0.045, in
even 18 mm steps, at the bowl and 80 mm further out.

## Traps

- **The descent is optional.** At the height the hand stands over the bowl the
  ball is already above the rim and inside the mouth's 150 mm opening. Reaching
  the floor, or the exact centre, buys nothing that the release needs.
- **A refused descent over the bowl is a placement, not a lost ball.** The pads
  go on reading it — the same depths, the same displacement, the same load —
  because nothing has left the grip, and the ball is already inside the mouth.
  A program that reads the refusal as "the grasp is gone", or that skips its
  release because a move raised, is throwing away a ball it still has and has
  already delivered. What the pads say, not the move's verdict, is what
  [[grasp-verification]] reads.
- **The height is not worth chasing.** At seed 1 a grip 6 mm wider and a
  fivefold lighter load stopped at the same tenth of a millimetre, and the
  floor stop is a contact rather than a threshold, so a program that backs off
  to try again only spends the descent twice.
- **The pads end over the mouth, not over the rim.** 5-6 mm off the bowl's
  centre at seed 1, and the mouth's inner radius is 75 mm against a 30 mm ball
  radius on a floor plate 160 mm across: there is nothing left to clear before
  the ball drops in.
- **This ends short over the bowl, not over the balls.** The reach limit
  [[reach-band-at-ball-height]] measures is the arm over the box with nothing
  in the grip; this one happens with a ball held and does not move with load or
  gap.
