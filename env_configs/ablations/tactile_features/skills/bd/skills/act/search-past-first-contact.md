---
id: A21
name: search-past-first-contact
family: act
summary: On a blind search, descend to wrist ~0.177 before closing — the first contact marks the object's top, ~25 mm higher.
task_types: []
tasks: [blind_pick]
channels: [depth, coincidence]
objects: the 50 x 50 x 60 mm block on the blind box's floor
evidence: In the seed 3-10 batch (2026-10-19), seeds 5, 6, 8 and 9 closed at wrist 0.200-0.205 where the contact flag first fired, and every one read the far plane (34.00) after the first 50-75 mm lift; seeds 1 and 2 descended to wrist 0.177 and held at 26.3-27.8 mm. The block is 60 mm tall on a floor at 0.016 m, so its top is at ~0.077 and its mid-height at ~0.047; the gripper centre sits 0.131 m below the wrist, which puts mid-height at wrist ~0.177.
---

## How

The contact flag is a range event: it fires when the pads' view first meets
the block, which on a top-down search is the block's TOP. Closing there grips
the top few millimetres of a 60 mm block — the pads pinch an edge and the
first lift drops it.

Descend by commanded position to the block's mid-height before closing:

```python
while wrist_z > 0.177 + 0.004:
    move_delta(dz=-0.008, coord="world")
    wrist_z -= 0.008
close_gripper()
```

The extra 25 mm is the gap between the flag's first firing (wrist 0.200-0.205)
and mid-height (0.177). At 0.177 the pads span the block's middle, with the
50 mm-wide side faces to press on; the close is [[search-close-default]].

## Traps

- **A close that reads fine on depth can still lose the block on the lift.**
  Seeds 5, 6, 8 and 9 all passed their own `depth < 28` check at the top edge,
  then read 34.00 on the first lift step. Depth says something is between the
  pads, not where on the block it is — see [[grasp-verification]].
- **The flag may never fire on a column that would hold.** A pad sees only
  what is between the fingers: above the block's top the flag stays down, and
  a switch that waits for it closes zero times (seed 7 tried 12 candidates at
  wrist 0.211 and never closed). Descend on position, not on the flag.
- The loop stops within 4 mm of 0.177 rather than commanding a sub-2 mm last
  step; a residual under 2 mm ends the loop ([[small-step-insertion]]).
