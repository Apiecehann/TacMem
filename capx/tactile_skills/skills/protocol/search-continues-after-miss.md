---
id: R14
name: search-continues-after-miss
family: protocol
summary: After a recoverable motion failure, open/retract if permitted and try another site; stop after native termination or budget exhaustion.
task_types: []
tasks:
- blind_pick
- blind_pick_balls
channels: []
objects: the hidden block in the blind box
evidence: Original R8 separates a recoverable missed candidate from an ended episode.
---

## How

Handle each candidate attempt independently and check whether the native environment still permits actions.

## Traps

Do not continue after native termination or repeat an indefinitely failing motion.
