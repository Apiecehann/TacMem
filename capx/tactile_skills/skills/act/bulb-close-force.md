---
id: A18
name: bulb-close-force
family: act
summary: Close the bulb at force_threshold=27.2 — with no threshold the fingers run to their limit and grip nothing.
task_types: []
tasks: [bulb_tightening]
channels: [depth, force]
objects: the light bulb
evidence: A threshold-free close shows contact and the bulb sitting between the pads while carrying nothing — every commanded twist slips over the glass, the bulb does not turn, and the squeeze climbs until the motion aborts on its pad-force guard.
---

## How

```python
close_gripper(force_threshold=27.2)
```

Read the depths the close returned: the grip has taken when the deeper pad is
clearly below 30 mm, well inside the 34 mm no-contact far plane.

## Traps

- **This task sets no default force.** The Args promise a task default (firm)
  when `force_threshold` is None; here that close has no tactile stop at all.
- **An ungripped bulb cannot tell you it is ungripped.** It still stands
  untouched in its socket, so the pose reading is the same either way. Settle
  the grip before the first twist rather than after it fails
  ([[grasp-verification]]).
