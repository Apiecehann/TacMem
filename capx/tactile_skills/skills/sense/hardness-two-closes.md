---
id: S7
name: hardness-two-closes
family: sense
summary: On the 15 mm block, hardness is the depth gained between position 0.10 and 0.06 — ~1.6 mm hard, ~0.8 soft, split 1.2.
task_types: []
tasks: [hardness_classify]
channels: [depth]
objects: blocks on this rig; the two positions bracket the object width
evidence: Same-seed differentials separated the two blocks with no overlap, and the differential cancels wherever the first close landed, which a single depth reading cannot.
copies:
  - docs/univtac_api_reference.md §2 and §3.4
---

## How

Close with the position commanded and the servo off, read the minimum pad
depth, then close again to a deeper position and read again:

```python
close_gripper(position=0.10, adaptive=False); d1 = min(left, right)
close_gripper(position=0.06, adaptive=False); d2 = min(left, right)
hardness = d1 - d2
```

A hard object passes the extra travel through to the gel (~1.6 mm); a soft one
yields and absorbs it (~0.8 mm). Split at 1.2 mm.

## Traps

- **The differential is the measurement, not the depth.** It cancels wherever
  the first close happened to land, which is the part the servo does not
  control.
- **Adaptation off is a precondition, not a preference.** With the servo on, a
  soft object is driven to the hard stop and reads as the firmest one in the
  set — see [[position-mode-for-yield]].
- The two positions bracket the object's width, so they are object-specific.
  Re-derive them for a new object rather than reusing the numbers.
- Hardness has to be read much firmer than weight. Both blocks land at nearly
  the same depth under a light grip; a single close at the weight grip carries
  about an eighth of the signal this differential does.
