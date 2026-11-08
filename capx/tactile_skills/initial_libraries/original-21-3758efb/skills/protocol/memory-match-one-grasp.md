---
id: R11
name: memory-match-one-grasp
family: protocol
summary: One progressive squeeze per can — 0.50 touches nothing (both pads 34.0 mm), 0.25 is the contact close, both adaptive off; then lift 2 cm and a ±2 mm sag pair.
task_types: []
tasks: [tactile_memory_match]
channels: [depth, anisotropy, coincidence]
objects: the 4 cm orange cans, rigid and compliant
evidence: 0.50 does not touch the can (both pads read 34.0 mm) and 0.25 lands at 21.3 mm on a rigid can against 26.6 mm on a compliant one — 12.7 mm against 7.4 mm of depth gain, no overlap and nothing near the 10 mm split. The same two closes carry the texture read at no extra close, and the lift carries the load response. 2026-11-07 gpt-6-astra trials 21 and 22 both required contact on the 0.50 close, so every probe was thrown away right after the 0.25 close with the reading already in hand, and neither trial ever placed a can.
---

## How

Approach the can once from a safe hover, then keep the fingers closed through
the whole sequence — do NOT prepend the standalone force_threshold weight
recipe:

```python
close_gripper(position=0.50, adaptive=False)   # 1. loose: both depths 34.0 mm
close_gripper(position=0.25, adaptive=False)   # 2. firm: real contact, < 33.0 mm
z0 = get_object_pose(can)[0][2]; move_delta(dz=0.020)   # 3. lift 2 cm
z1 = get_object_pose(can)[0][2]                          # how far the CAN followed
move_delta(dz=0.002); move_delta(dz=-0.002)              # 4. sag pair, same grasp
# 5. return the can to rest before opening
```

One squeeze supplies every feature: loose minus firm depth for hardness
(split 10 mm), firm minus loose anisotropy for texture (split 0.02,
[[can-texture-difference]]), and follow plus sag for the load response.

## Traps

- **Validity comes from a loose read that IS the far plane — 34.0 mm on both
  pads, no contact — followed by a firm read in contact.** A pad in contact at
  the 0.50 close means the can is not between the fingers; it is not a stricter
  reading to accept. Low follow-through does not invalidate the reading,
  symmetry is not required, and not every property channel has to vote
  ([[follow-is-signal]]).
- **Do not carry the block's numbers here.** `position=0.10` and `0.06` are both
  already past a 4 cm can, so both closes bottom out and the difference
  collapses to ~0 for either hardness — a blind reading that reads like a
  confident answer ([[no-calibration-transfer]]).
- Transport the chosen can with a separate, firmer re-grasp: a 300 g rigid can
  slips out of the sensing grip, so close near 27.7 for the carry
  ([[grip-force-by-mass]]).
