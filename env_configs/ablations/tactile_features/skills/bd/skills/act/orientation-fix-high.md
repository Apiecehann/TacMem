---
id: A17
name: orientation-fix-high
family: act
summary: Straighten a held object high up, in the tool frame, in small verified steps.
task_types: [manipulate, insert]
channels: []
objects: any
evidence: The server rejects a twist that did not actually happen, which is how a slipping grasp is caught before the object is carried anywhere.
copies:
  - univtac_skill/SKILL.md (orientation fix-ups, control workflow)
  - docs/univtac_api_reference.md §3.2 and §3.5
---

## How

Rotate the held object about the gripper's own axis for anything large
(≳0.3 rad), split into small steps and checked between them. Do it while well
clear of the table and of the target.

## Traps

- A world-frame rotation swings the whole arm rather than turning the object in
  place ([[world-frame-transport]] has the number).
- Rotating near the target catches the object on whatever it is about to be
  placed on or into.
- The rotation is verified against what actually happened: at least 30% of the
  requested angle (with a floor of 0.05 rad) or the call fails and reports that
  the object may not be firmly gripped. That failure is information — re-grasp
  rather than repeating the twist harder.
- Judge a tilt from the camera image. A non-privileged pose reading returns an
  identity orientation and will report a tilted object as square.
