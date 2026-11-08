---
id: P4
name: descend-until-supported
family: place
summary: Lower in small steps until the object's own height stops falling — never press past.
task_types: [classify, insert, manipulate]
channels: [depth]
objects: any
evidence: The held object's height is the only thing that answers "is it supported"; a slow press compresses the gel rather than shearing it, so the tactile slip channel stays quiet the whole way down.
copies:
  - docs/univtac_api_reference.md §3.4
  - univtac_skill/SKILL.md
---

## How

Step down ~5 mm at a time and read the object's own height between steps. The
first step that does not lower it means the object has met its support.

## Traps

- **Slip is not support.** `detect_slip` will not tell
  you the object landed: a slow press compresses the gel, the displacement
  growth stays under threshold, and the loop keeps descending until the fingers
  wedge under the object and push it out. Watch the height, not the slip.
- Do not press after the height stops falling. The extra travel goes into the
  object or into whatever it landed on.
- The height to aim for is computed, not discovered: the support's surface plus
  the object's own half-height. See [[seat-height-from-plate]].
