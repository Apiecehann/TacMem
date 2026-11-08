---
id: S23
name: texture-in-relief
family: sense
summary: 'Read height_roughness_mm at position=0.6, adaptive=False: smooth about 0.02-0.05 mm, dotted 0.09-0.25
  mm, historical boundary 0.06; off-centre samples may under-read.'
task_types: []
tasks:
- blind_pick_balls
channels:
- relief
objects: the twelve balls in the blind box, measured at a 48 mm seat between the pad faces
evidence: Retained applicable rules from fixed source S23; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

The commanded opening is 48 mm. Catch a blocked-close RuntimeError and, if the episode remains active, sample the two pads where they stopped. Compare their height_roughness_mm values at this fixed opening; use the larger valid reading because the two pads need not agree. The historical boundary belongs to this seat, not an arbitrary adaptive squeeze. Off-centre dotted samples were about 0.07-0.08 mm, so a low response may be a missed seat. Keep missing observations and uncertain seating explicit; an exception alone does not verify a ball.
