---
id: S23
name: texture-in-relief
family: sense
summary: Seat at close_gripper(position=0.6, adaptive=False) — the raise is a ball, not an empty station — and read relief — smooth under 0.05 mm, dotted 0.09-0.25, the bar 0.06.
task_types: []
tasks: [blind_pick_balls]
channels: [relief, depth]
objects: the twelve balls in the blind box, measured at a 48 mm seat between the pad faces
evidence: The task's own expert separates the two populations on `height_roughness_mm`, the RMS relief left after a smooth quadratic contact envelope is fitted to the indentation map and removed. Bench, all twelve balls of one scene at a commanded 48 mm seat — smooth (rubber and polymer) 0.016-0.027 mm, dotted 0.092-0.093 mm. Across the real search the same split held — smooth 0.017-0.019, dotted 0.079-0.093 — and no smooth ball crossed the 0.060 bar. A dotted ball measured at 0.040 mm was a shallow seat, not a smooth ball; seated properly it read 0.079-0.093. The 2026-10-25 oracle run read at that same seat, smooth 0.011/0.022/0.040 against dotted 0.086. The bar is a seat reading — on 2026-10-25 (seed 1) the program closed adaptively to a 26.5 mm depth instead, the ball gave way until the pad faces were 31-33 mm apart (123 N a side, measured from the task's own pad forces), and the smooth rubber ball in that crush read 0.157/0.175 mm — above every dotted reading on record. The oracle's own seats over one scene gave smooth 0.019-0.038 and dotted 0.088-0.246, and the 2026-10-25 search read its right pad at 0.091-0.099 on four seats while the left pad read 0.044 on two of them and 0.140-0.146 on the other two. Off-centre the same seat under-reads. On 2026-10-25 (seed 1) the non-privileged search parked three seats 17.3, 21.2 and 21.4 mm from a dotted ball's centre (distances from the trace's own per-ball records) and the pads bit its edge at 20.6/14.0, 1.4/3.8 and 0.0/0.5 N against 115.8/115.5 N for a dotted ball under centred pads earlier the same day — the readings were 0.054/0.081, 0.048/0.068 and 0.040/0.068, the better pad 0.068-0.081 against 0.214/0.195 centred. Depth did not separate the two states (27.4-27.8 off-centre against 27.0-27.1 centred), so the relief is the only field that shows the miss. The task's API reports the field as `left/right_height_roughness_mm` and is the only surface-shaped quantity it carries. A raise is the seat's own signature, not a failed measurement — on 2026-10-25 (seed 1) seven closes at the commanded 48 mm landed between 50.0 and 53.8 mm and every one of them raised — those seven stations were 4.2-6.2 mm from a ball's centre, two of them dotted balls held at 128.0 and 128.7 N with depths of 26.60-27.20 mm — and the run's program caught each raise and went on to the next station without reading the pads, so seven real seats were thrown away and its only readings that episode were grazes of 0.005-0.053 mm.
copies:
---

## How

The imprint of a sphere is a clean spherical cap; a dotted ball leaves its
bumps in the gel. Fit a smooth quadratic envelope to the indentation map and
subtract it — what is left over, root-mean-squared, is the relief. That is the
number the API already computes: `left_height_roughness_mm` and its right twin.

The number belongs to a seat, so the seat is what has to be fixed. Close by
finger travel, not by gel depth:

```python
try:
    close_gripper(position=0.6, adaptive=False)   # 0.6 puts 48 mm between the pad faces
except RuntimeError:
    pass                                          # a ball stops it 1-6 mm wide: the raise is the ball
reading = get_tactile_data()                      # read the pads where they stand
```

0.6 is 60 percent of the 80 mm full opening. Between the pads a resting ball
60 mm wide leaves the pads biting it by about 12 mm, which is deep enough to
develop the imprint. Read it in millimetres:

| reading | surface |
|---|---|
| ~0.02-0.05 | smooth (rubber or polymer) |
| ~0.09-0.25 | dotted / textured |
| 0.06 | the bar between them |

Take the larger of the two pads. One ball gives two different numbers — a
dotted ball on 2026-10-25 read 0.093 on the right pad and 0.146 on the left
at the same seat, and 0.091 against 0.044 at another site — because the
surface is seen where the imprint is developed, not on the pad that only
grazed it. Requiring both pads to agree throws the ball away.

The pads have to straddle the ball's centre. The imprint is a cap; bite the
ball's edge instead and the cap is a sliver of it. A dotted ball measured
17-21 mm off its centre still blocked the close and still read a depth of its
own (27.4-27.8 mm, in fact deeper than the 27.0-27.1 of the centred seat), but
the squeeze was 0-20.6 N a side against 115 N, and the better pad read
0.068-0.081 mm against 0.214. Depth cannot tell these two states apart. That
0.07-0.08 band sits between the smooth ceiling and the dotted floor, and what
it marks is a missed seat: the ball is worth another seat, not a verdict.

Two closes, two jobs. The seat above is the measurement. Once the material is
decided, a second close at a gel-depth target grips the ball for the lift; by
then the crush no longer matters, because nothing more is read from it.

## Traps

- **An adaptive close is not a seat.** Its landing is set by the ball, not by
  you: the servo keeps closing until the gel reads the target depth, so a
  compliant ball travels much further before it gets there — 31-33 mm between
  the pad faces on the rubber ball of 2026-10-25 against 47 mm on a dotted one
  the day before, for the same reading. Each ball is then measured in a
  different state of its own deformation, and the relief is no longer a
  surface property: the squeeze leaves the ball flattened and creased, and the
  residual reads 0.157 where the same ball seated read 0.02.
- Relief answers a different question from depth. Depth is how hard the pad is
  pressed, and the same depth can mean a light touch on a hard ball or half a
  soft one squashed flat ([[depth-grip-scale]]). Relief says what the surface
  is shaped like — but only while the ball still is one.
- The imprint has to be developed. A graze reads low — a dotted ball measured
  at 0.040 mm while barely seated, and 0.079-0.246 once seated with 0.6-2.3 mm
  of indentation. The bar is set high enough that a shallow seat cannot reach
  it: at the 58 mm rake gap dotted balls read 0.031-0.034 against smooth
  0.000-0.009, and a brushed contact reads 0.008-0.020 against 0.002-0.019 —
  fully overlapping. Both errors are real; the shallow one only costs another
  site, the crushed one delivers the wrong ball. An edge bite is a third form
  of the same error and the easiest to mistake for smooth: 17-21 mm off a
  dotted ball's centre the better pad reads 0.068-0.081, which clears the 0.06
  bar but not a bar pushed up to 0.085 — the 2026-10-25 search set 0.085 and
  threw away three dotted balls for it.
- A ball the pads have shoved sits under the edge of the pad, not its centre,
  so the imprint is partial and under-reads ([[pass-ball-with-wide-pads]]).
- A seat that closes to nothing returns the far plane on both pads, and its
  relief is empty rather than smooth ([[wide-pads-see-nothing]]).
- The field is reported only where the fingertip exposes a full height map; if
  it is missing from a reading, that pad has no answer and is not a zero.
