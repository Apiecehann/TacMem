---
id: P3
name: seat-height-from-plate
family: place
summary: Derive placement height from allowed object/support poses, surface height and base-versus-centre origin conventions.
task_types:
- classify
- manipulate
not_tasks:
- blind_pick_balls
channels: []
objects: any
evidence: Original P3 documents base-versus-centre pose conventions and the resulting placement arithmetic.
---

## How

For a base-origin object, place its base at the support surface; for a centre-origin object, add half its vertical extent. A base-origin support surface is its base height plus its thickness.

## Traps

Check the origin convention for each object. Do not use this rule when object/support position queries are unavailable.
