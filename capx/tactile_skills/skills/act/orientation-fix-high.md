---
id: A17
name: orientation-fix-high
family: act
summary: Correct orientation high above obstacles, using small tool-frame rotations and ordinary scene vision.
task_types:
- manipulate
- insert
channels: []
objects: any
evidence: Original A17 recommends tool-frame correction away from the target.
---

## How

For large corrections, split the rotation into small commands. Keep the object clear of nearby supports and obstacles.

## Traps

A world-frame rotation may swing the arm position. A refused motion is not proof that the object rotated.
