---
id: A25
name: reach-band-at-ball-height
family: act
summary: Ball height is reachable only between x≈0.39 and 0.62 — outside it the pads stop above every ball's
  centre, both gels on 34.00, the arm, not a ball.
task_types: []
tasks:
- blind_pick_balls
channels:
- depth
objects: the twelve balls in the blind box
evidence: Filtered from the fixed original skill A25; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

The arm's reach ends before the box's near wall does. At the near end the
hand trades depth for horizontal travel: commanded lower, it comes back both
higher and further east, so the pad centre cannot be put on a ball's centre
(z = 0.050) west of about x = 0.377.

Measured with the hand pointing straight down and the pads 80 mm apart, every
descent commanded to the pad centre 0.042:

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
