---
id: A25
name: reach-band-at-ball-height
family: act
summary: Ball height is reachable only between x≈0.39 and 0.62 — outside it the pads stop above every ball's centre, both gels on 34.00, the arm, not a ball.
task_types: []
tasks: [blind_pick_balls]
channels: [depth]
objects: the twelve balls in the blind box
evidence: Measured 2026-10-24 against the live seed-1 server (blind_pick_balls, one episode, hand straight down, pads 80 mm apart) by commanding the flange to 0.16 -- pad centre 0.118 below it -- at a grid of (x, y) and reading where the hand stopped; the task's own physical_trace.jsonl gave the exact TCP. Near edge, y=-0.115, lowest pad centre reached (where the hand ended, both gels, load) -- 0.365 -> 0.0670 (x=0.394, 34.00/34.00, 0.0), 0.375 -> 0.0523 (x=0.392, 34.00/34.00, 0.0), 0.380 -> 0.0476 (x=0.390, 34.00/34.00, 0.0), 0.390 -> 0.0432 (on command, arrived). The same 0.365 command in a separate earlier episode stopped at 0.0668 on y=-0.115 and 0.0679 / 0.0677 on y=0.000 / +0.115, all three with 34.00/34.00 and load 0, so the limit depends on x, not on the row. Columns, y=-0.115 and y=0.000 / +0.115 for the last one -- 0.468 -> 0.0433, 0.546 -> 0.0432, 0.624 -> 0.0431 / 0.0431 / 0.0431, all arriving. Far edge 0.655 -> 0.1116 at x=0.6568, 34.00/34.00, load 0. The first LLM run on this task (gpt-6-astra, seed 1, 2026-10-24) is the in-run case -- its third call aimed the pads at 0.0627 at x=0.365, the arm stopped at 0.0724 (9.7 mm high, 13 mm east), both gels 34.00, and the server answered Motion planning failed after the whole per-move budget.
copies:
---

## How

The arm's reach ends before the box's near wall does. At the near end the
hand trades depth for horizontal travel: commanded lower, it comes back both
higher and further east, so the pad centre cannot be put on a ball's centre
(z = 0.050) west of about x = 0.377.

Measured with the hand pointing straight down and the pads 80 mm apart, every
descent commanded to the pad centre 0.042:

| commanded x (y = -0.115) | lowest pad centre | where the hand ended | both gels |
|---|---|---|---|
| 0.365 | 0.0670 | x = 0.394 | 34.00, load 0.0 |
| 0.375 | 0.0523 | x = 0.392 | 34.00, load 0.0 |
| 0.380 | 0.0476 | x = 0.390 | 34.00, load 0.0 |
| 0.390 | 0.0432 (arrived) | on command | 34.00, load 0.0 |
| 0.468 | 0.0433 (arrived) | on command | 34.00, load 0.0 |
| 0.546 | 0.0432 (arrived) | on command | 34.00, load 0.0 |
| 0.624 | 0.0431 (arrived) | on command | 34.00, load 0.0 |
| 0.655 | 0.1116 | x = 0.657 | 34.00, load 0.0 |

All three public rows stop at the same height on the near edge (0.067-0.068
at x = 0.365), and the last column reaches on all three, so the band is a
function of x only.

The floor is soft, not a wall. It moves with what you ask for: at x = 0.365
a command to 0.0627 stopped at 0.0724, and a command to 0.042 stopped at
0.0670 — asking 20 mm lower bought about 5 mm, and the shortfall went from
9.7 mm to 25 mm. A descent aimed at a height it cannot hold therefore does
not fail at one fixed z; it fails with the pads somewhere between the ball's
top (0.080) and a few millimetres below its centre.

The arrival verdict is 1.5 mm (2.5 mm before 2026-10-25), so a descent can
still be refused while being perfectly usable: at 0.380 the pads reached
0.0476 — 2.3 mm below the ball's centre — and the call answered Motion
planning failed, 7.7 mm from the commanded 0.042. A move that stalls within
5 mm of its goal is no longer a refusal: since 2026-10-26 it reports success
and puts the shortfall in the message ("OK: the hand stopped 2.9 mm short of
the commanded point"), while a real refusal says how far it travelled. Judge
the height from where the hand is, not from the message alone.

## Traps

- **Both pads on the far plane is not proof the box was empty.** Outside the
  band the stop is the arm's: nothing is there to be met. Inside it the same
  signature can be a ball — pads left fully open clear a 60 mm ball by 10 mm,
  so the gels stay at 34.00 while the pad's body comes down on it
  ([[wide-pads-see-nothing]]). Once a close has brought the pads in toward a
  ball, a pad that leaves 34.00 is contact, which is what
  [[depth-grip-scale]] and [[grasp-verification]] read.
- **A refused descent leaves the hand somewhere else.** The eastward return
  also shows up on refusals — five of them on 2026-10-25 ended with the hand
  19-25 mm east of where it started, over 800+ control steps — so the x a
  failed station was given is not the x the next station is measured from.
- **Do not read the failure as "no ball here".** A row the pads could not be
  lowered into was never searched at all; the same failure over a different x
  may be the edge of the band rather than the end of the row.
- **The hand is not where the command said.** The eastward return grows as
  the depth does — 9.5 mm at x = 0.380, 16.6 mm at 0.375, 29 mm at 0.365 —
  and y drifts up to 3 mm with it. Any x carried forward from a failed
  descent is wrong; read the pose back before the next call rather than
  assuming the command took.
- **The band is the arm's, not the box's.** The near wall is at x = 0.330 and
  the pads were still 30-50 mm clear of it at every stop, so "back away from
  the wall" is not the fix; the fix is to search where the arm can reach.
- **A last column can land inside the near edge.** The reset re-draws the
  grid, so a column near x ≈ 0.37 is possible; a ball there is still
  graspable (0.375 stops 2 mm above its centre) but not at the same height a
  column in the middle of the box uses. [[lattice-pitch-budget]] is the same
  question on the far side.
