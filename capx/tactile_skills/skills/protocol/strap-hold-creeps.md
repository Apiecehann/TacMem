---
id: R15
name: strap-hold-creeps
family: protocol
summary: A held strap reads most of a band higher over three seconds without loading more — hold the height and the clock, not the reading.
task_types: []
tasks: [tension_strap]
channels: [shear]
objects: the elastic strap
evidence: Standing still at 12.7 N through eight no-op moves (330 physics ticks), the task's physical tension fell 0.05 N while the marker difference climbed 0.47 px. Re-zeroing the estimate at the end of the first hold put the second stage at 18.06 N on the gel and 17.65 N physically, both inside the half-newton band. The drift also does not carry. On a later run the first hold climbed 0.26 px over its 369 ticks, and the next stage's load then matched the static gel line with no offset at all — a rate subtracted from the reading by elapsed time put that stage 0.8 N high, measured two ways (the hand sat 3.2 mm above the held height, +6.8 N at 2.12 N/mm, and the gel read 18.8 N against a band topping out at 18.5).
---

## How

A hold is made of motion calls, not of waiting: nothing advances the
simulation while the hand is idle, so a hold is the same pose asked for
again and again. About ten such calls is the three seconds a stage needs.

Hold the height, not the reading. The load a held strap carries does not
change on its own, so there is nothing for a hold-time loop to correct.

Before the next stage, re-zero the estimate where the load is known — the
stage just held — so the creep accumulated during the hold does not become a
bias in the next target.

## Traps

- **The creeping reading looks like a load that is rising.** Correcting on it
  drives the hand down and turns a good hold into a real release, and watching
  it against a band abandons a hold that was fine: on a 2026-10-31 run the
  gauge climbed 0.50 N in 287 ticks while the hand did not move, and the
  program's own +-0.4 N hold guard raised on it.
- **It is a settling, not a clock.** The drift does not accumulate from one
  stage to the next, so subtracting a per-tick rate from every later reading
  makes the next stage land most of a newton high. Re-zero where the load is
  known; do not carry a rate.
- **The creep is in the gel, not in the strap.** The physical tension is
  flat to 0.05 N over the same window, so a second reading does not mean the
  load moved ([[differential-across-states]]).
- **A hold that is cut short is not partially credited.** The stage needs its
  three seconds continuously, so more calls than the arithmetic minimum is
  the safe direction and the extra ones cost nothing.
