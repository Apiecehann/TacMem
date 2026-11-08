---
id: A18
name: bulb-close-force
family: act
summary: Close the bulb at force_threshold=27.2 — the unservoed default lands millimetres off.
task_types: []
tasks: [bulb_tightening]
channels: [depth, force]
objects: the light bulb
evidence: An explicit force_threshold turns the close into a servoed landing — the fingers micro-step onto the target and the depth comes back at it, spread about ±0.05 mm, so the reading after the close is the reading that was asked for. With no threshold the close falls back to the robot's own default depth (27.5 mm on this rig) and to the unservoed landing that goes with it — it overshoots by a few millimetres and comes back different on every grasp, so no depth from it can be carried to the next one.
---

## How

```python
close_gripper(force_threshold=27.2)
```

Read the depths the close returned: the grip has taken when the deeper pad is
clearly below 30 mm, well inside the 34 mm no-contact far plane.

## Traps

- **The default is not this task's.** The Args promise a task default (firm)
  when `force_threshold` is None, and the close does take one — the robot's own
  depth, unservoed — so it lands a few millimetres deeper than 27.2 does, and
  not at the same depth twice.
- **An ungripped bulb cannot tell you it is ungripped.** It still stands
  untouched in its socket, so the pose reading is the same either way. Settle
  the grip before the first twist rather than after it fails
  ([[grasp-verification]]).
