---
id: S24
name: wide-pads-see-nothing
family: sense
summary: A 60 mm ball between fully open pads clears each gel by 10 mm and reads 34.00 like free air — but only within ~10 mm of the axis; further out it lies under the pads, and a descent there stops on it.
task_types: []
tasks: [blind_pick_balls]
channels: [depth]
objects: the 60 mm balls of the blind box; the clearance is half the jaw opening minus the ball's radius
evidence: Measured on the 2026-10-24 blind_pick_balls episodes. In seed 2 the jaws sat at 76-80 mm while a ball's centroid was within 12 mm of the finger axis — 365 logged frames, every one 34.00 on both pads — and the closest of those descents had the ball's top 10 mm above the pad centre, the ball squarely between the pads. In the same episode 30 frames with the jaws that wide caught one gel against a ball and read 26.8-29.0 on that pad against 34.00 on the other, so what a pad reports follows its clearance to the ball, not the jaw command. In seed 1, the run that lost all three rows, all 1660 frames stayed at 34.00 on both pads, 143 of them with a ball within 20 mm of the finger axis and its centre within 15 mm of the pad height. The scale is the documented one — 34.00 at the far plane, 28.3 at the gel's own face, so a pad reaches about 5.7 mm past the gel.
---

## How

At full open each jaw stands 0.04 m off the finger axis, which puts the two gel
faces 80 mm apart. A 60 mm ball sitting between them therefore keeps 10 mm off
each gel, and the depth scale runs only from 34.00 (the far plane) down to
28.3 mm (the gel's own face) — about 5.7 mm of reach. Ten millimetres of
clearance is outside that, so both pads report the far plane while the ball is
directly between them.

A descent onto a ball looks exactly like that: 34.00 on both pads all the way
down, including the heights where the ball's top is already above the pad
centre, and the reading changes only once the jaws have closed far enough for a
gel to come near the ball.

The straddle has a width of its own. Each jaw stands 40 mm off the finger
axis, so a 60 mm ball fits between the pads only while its centre is within
about 10 mm of that axis. Further out the ball is no longer between the gels —
it is under the pad's own body, 10 mm and more clear of the gel face and so
still 34.00 — and the pads cannot be lowered through it. Five open-pad
descents on two runs (2026-10-25, seed 1, the task's own physical_trace.jsonl)
stopped with the pad centre at 0.088-0.094, 38-44 mm above the 0.050 a seat
needs, both gels 34.00, while stations 30-90 mm away on the same row arrived at
0.050; each of the five ended after the hand had crept 19-25 mm east over 800+
control steps with the command unchanged. What the pads stopped against was
not identified — a ball under them is the one thing those stations had that
the others did not, and it is the only body in the box that reaches 0.09
([[reach-band-at-ball-height]] measures where the arm alone stops).

## Traps

- **Both pads at 34.00 is not an empty mouth.** It is the ordinary reading with
  a ball between wide-open pads, and it is the same number free air gives. A
  sweep that walks with the jaws open and treats 34.00 as "nothing here" passes
  over every ball in the box without a reading.
- **A descent that stops short with both pads at 34.00 is not always the
  arm.** Inside the reach band, over a ball, a stop 40 mm above the seat is a
  ball under the pads; the hand also ends up some 20 mm off the x it was given,
  so the coordinates carried forward from that station are wrong.
- **One pad in the 20s against 34.00 on the other is a graze, not a grasp.**
  That is a single gel against the ball while the gap is still wider than the
  ball; it says where the ball is, not that the fingers have it.
- **The closing gap is the measurement, not the opening one.** The depth turns
  informative as the pads come in toward the ball's width — a gap near the
  ball's diameter is what takes the pads from blind to sensitive. Reading the
  depth at the widest opening samples the one state in which a ball between the
  pads cannot appear.
- **A blocked close finds what the gel cannot see.** A close that stops short
  of the opening it commanded raises, and its message carries the opening it
  landed on; that short stop is the fingers meeting the ball's width, and it
  works at clearances the gel is blind at.
- The clearance is half the opening minus the ball's radius, so the number
  moves with the object and with how far the jaws are open
  ([[no-calibration-transfer]]). Ten millimetres at full open belongs to a
  60 mm ball; a ball first comes inside the pads' reach with the gap down near
  its own diameter, so a reading taken at 80 mm is the least informative one
  the pads can give about what is between them.
- The far plane is also what a dropped object reads, which is why the contact
  flag cannot stand in for a measurement here ([[contact-flag-is-range]]).
