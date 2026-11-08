---
id: R16
name: weigh-grasp-by-lift
family: protocol
summary: At the 28.5 weighing grip a block that stayed on the table reads as light — lift 3 cm and check the block follows, re-closing at ~28.0 and lifting again if it does not.
task_types: []
tasks: [weight_classify]
channels: [depth, coincidence]
objects: the 15 mm block
evidence: 2026-11-07 gpt-6-astra trial 1 (seed 1) closed at 28.58/28.28 mm and aborted before lifting; the video's last frame shows the block standing between the pads. The oracle recipe verifies the hold the same way this skill asks — after a 3 cm lift it treats the block's z staying under 0.02 as a missed grasp and repeats the close at 28.0 — and its 20 seeds held at 28.19-28.50 mm on the deeper pad.
---

## How

Close at ~28.5 and read the resting baseline. Lift ~3 cm and read the block's
height — a caught block rises with the hand; one still on the table was not
caught. The lift is the check; the close's marker displacement is not.

If the block did not follow, open, re-approach in one call, and close at ~28.0,
then lift again. The same close twice returns the same reading, so only a
firmer one changes the answer.

## Traps

- **The close's displacement is not the check.** At the weighing grip the pads
  sit at the gel face, so the marker rise is small however well the block is
  held. Whether the block is held is answered by the lift: a caught block rises
  with the hand; one still on the table did not.
- Do not squeeze firmer to force a bigger signal. By ~26 the shear collapses
  ([[grip-force-by-mass]]); the 28.0 close is the fallback only after the lift
  shows the block was left behind.
- The block is light enough for the gel to carry at the gel face
  ([[depth-grip-scale]], [[keep-closed-if-task-allows]]), so judge the hold by
  what the lift does, not by how hard the close looks.
