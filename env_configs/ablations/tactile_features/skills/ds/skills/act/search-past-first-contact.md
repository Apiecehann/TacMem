---
id: A21
name: search-past-first-contact
family: act
summary: On a blind search, descend to wrist ~0.177 before closing — the first contact marks the object's top,
  ~25 mm higher.
task_types: []
tasks:
- blind_pick
channels: []
objects: the 50 x 50 x 60 mm block on the blind box's floor
evidence: Filtered from the fixed original skill A21; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

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
