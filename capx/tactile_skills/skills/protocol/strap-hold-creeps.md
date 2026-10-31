---
id: R15
name: strap-hold-creeps
family: protocol
summary: A held strap reads higher without loading more — hold the height, and re-zero the reading between the two stages.
task_types: []
tasks: [tension_strap]
channels: [shear]
objects: the elastic strap
evidence: Standing still at 12.7 N through eight no-op moves (330 physics ticks), the task's physical tension fell 0.05 N while the marker difference climbed 0.47 px — half a newton of reading for no change in load, about 0.4 N for the three seconds a stage lasts. Re-zeroing the estimate at the end of the first hold put the second stage at 18.06 N on the gel and 17.65 N physically, both inside the half-newton band; without it the second stage lands 0.6 N low.
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
  drives the hand down and turns a good hold into a real release: half a
  newton of reading over a stage is twice the margin the target allows.
- **The creep is in the gel, not in the strap.** The physical tension is
  flat to 0.05 N over the same window, so a second reading does not mean the
  load moved ([[differential-across-states]]).
- **A hold that is cut short is not partially credited.** The stage needs its
  three seconds continuously, so more calls than the arithmetic minimum is
  the safe direction and the extra ones cost nothing.
