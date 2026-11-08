---
id: S23
name: travel-controlled-ball-seat
family: sense
summary: Use a fixed 48 mm seat — close_gripper(position=0.6, adaptive=False) stops on a ball and raises; that raise is a ball, not an empty station, and the pads read anyway.
task_types: []
tasks:
- blind_pick_balls
channels:
- depth
objects: the twelve balls in the blind box, measured at a 48 mm seat between the pad faces
evidence: The original position-controlled seat and read-after-stop rules are retained. Material thresholds, surface-based validity checks and their evidence remain in the source snapshot.
copies: null
---

## How

Close by finger travel, not by gel depth:

```python
try:
    close_gripper(position=0.6, adaptive=False)
except RuntimeError:
    pass
reading = get_tactile_data()
```

0.6 is 60 percent of the 80 mm full opening. The pads have to straddle the
ball's centre.

Two closes, two jobs. The seat above is the measurement. Once the material is
decided, a second close at a gel-depth target grips the ball for the lift; by
then the crush no longer matters, because nothing more is read from it.

## Traps

- **An adaptive close is not a seat.** Its landing is set by the ball, not by
  you: the servo keeps closing until the gel reads the target depth, so a
  compliant ball travels much further before it gets there.
- A seat that closes to nothing returns the far plane on both pads
  ([[wide-pads-see-nothing]]).
