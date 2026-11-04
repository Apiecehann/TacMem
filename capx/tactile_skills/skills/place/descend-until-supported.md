---
id: P4
name: descend-until-supported
family: place
summary: With allowed object poses, descend incrementally; stop further descent when the object height stops following the hand.
task_types:
- classify
- insert
- manipulate
channels: []
objects: any
evidence: Original P4 describes checking object height during descent.
---

## How

Check object and hand displacement between moves and compare with the destination surface geometry.

## Traps

Do not use unavailable object poses in blind tasks. A failed move may be a planning failure; do not assume it proves placement.
