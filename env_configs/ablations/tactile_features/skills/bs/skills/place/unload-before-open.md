---
id: P5
name: unload-before-open
family: place
summary: Back up about 5 mm to unload the fingers before opening — never release while pressing.
task_types: [classify, insert, manipulate]
channels: [depth]
objects: any
evidence: Opening is a placement rather than a release — the two failure directions are pressing the object into the surface and dropping it from a height, and both are avoided by the same one-step unload.
copies:
  - docs/univtac_api_reference.md §3.4
---

## How

With the object resting on its support and the descent stopped
([[descend-until-supported]]):

1. back up ~5 mm in one step, taking the fingers' weight off it;
2. then `open_gripper()`.

## Traps

- Opening while the fingers still press the object into the surface tips and
  wedges it — the object is already supported, so the extra load has nowhere to
  go but sideways.
- Opening from a hover of ~1 cm or more drops it crooked. The useful height is
  the narrow band just above the surface.
- If an open does leave the object tipped or wedged, the recovery is not
  another open: lift ~3 cm, re-grasp and re-place.
- The returned contact flags tell you the **grip** released. They say nothing
  about whether the object is placed ([[settle-after-release]]).
- **Not for a part held by its own thread.** A threaded part is not resting on
  a support, so backing up does not unload it — it drags it out of its seat.
  On 2026-10-08 a run followed this rule with the bulb still gripped and took
  it 0.39 mm back out of its thread and 1.10° off square, the pad force going
  from 108 N to 145 N, past the 135 N the glass holds; the episode ended there.
  A threaded part is released where it stands ([[wrist-limit-segmentation]]),
  which is why this family does not route to `screw` (taxonomy.yaml).
