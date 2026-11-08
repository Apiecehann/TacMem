---
id: R15
name: strap-hold-creeps
family: protocol
summary: Hold the height and the clock, not the reading — over a hold the reading drifts by most of a band, and then recovers.
task_types: []
tasks: [tension_strap]
channels: [shear]
objects: the elastic strap
evidence: Holding 2.5 s at 11.9 N dropped the reading 0.45 px while the tension fell 0.07 N, and the points after the hold landed on the line the hold-free ramp drew — 19.30, 22.52 and 25.65 px at 12, 14 and 16 mm against 19.54, 22.56 and 25.67.
---

## How

A hold is a pose re-asked for, not a wait: nothing advances the simulation
while the hand is idle. One such call advanced the episode by about 41 physics
ticks — a 31-step plan plus the 10 the server always delays — against a 120 Hz
clock, so a 3 s stage is 360 ticks and lands inside ten calls, fourteen with
room; overshooting is harmless once the stage is counted.

Keep the height and count the calls. Do not correct on the reading during the
hold, and do not re-zero after it — the drift comes back on its own, so
re-zeroing turns it into an offset.
