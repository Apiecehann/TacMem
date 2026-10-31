---
id: R8
name: tactile-cannot-locate
family: protocol
summary: Confirm where an object ended up from an image or a pose read — the gel cannot tell placed from dropped.
task_types: [classify, force, insert, manipulate, screw, wipe]
# blind_pick_balls has neither an image in the observation nor a pose query:
# the skill's lesson is true there, its prescribed check is not available.
not_tasks: [blind_pick_balls]
channels: [depth, coincidence]
objects: any
evidence: Both pads return to the 34 mm far plane whether the object was released onto a surface or lost mid-carry, so the depth field cannot separate the two outcomes; the images and the object pose can.
copies:
  - univtac_skill/SKILL.md (resolution matching)
---

## How

Use the tactile channels for what only they can answer — is something between
the fingers, how hard is it being pressed, which way is it sliding — and use an
image or a pose reading for *where* an object ended up.

When the object's fate decides the next step, look at the picture. A tactile
reading never overrides what the image shows.

## Traps

- "Both pads at the far plane" means the hand is empty. It does not distinguish
  a successful placement from a drop, and it does not say the object is
  anywhere near where it was meant to go.
- The same blind spot runs the other way: a supported object still held in the
  fingers reads exactly like an object held in the air.
