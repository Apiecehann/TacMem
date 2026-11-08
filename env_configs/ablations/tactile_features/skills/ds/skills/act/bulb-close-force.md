---
id: A18
name: bulb-close-force
family: act
summary: Close the bulb at force_threshold=27.2 — the unservoed default lands millimetres off.
task_types: []
tasks:
- bulb_tightening
channels:
- force
objects: the light bulb
evidence: Filtered from the fixed original skill A18; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

```python
close_gripper(force_threshold=27.2)
```

## Traps

- **The default is not this task's.** The Args promise a task default (firm)
  when `force_threshold` is None, and the close does take one — the robot's own
  depth, unservoed — so it lands a few millimetres deeper than 27.2 does, and
  not at the same depth twice.
- **An ungripped bulb cannot tell you it is ungripped.** It still stands
  untouched in its socket, so the pose reading is the same either way. Settle
  the grip before the first twist rather than after it fails
  ([[grasp-verification]]).
