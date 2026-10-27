---
id: S16
name: contact-flag-is-range
family: sense
summary: Treat the contact flag as range, not a press — it fires ~5.6 mm before the surface reaches the pad.
task_types: [classify, insert, manipulate, screw, wipe]
# On blind_pick_balls the unloaded gel reads 28.3 mm against a 34 mm far plane,
# so the flag is true with nothing in range at all and carries no distance.
not_tasks: [blind_pick_balls]
channels: [depth, coincidence]
objects: any
evidence: The server computes the flag as depth < far_plane - 0.1, and the depth scale runs from the 34 mm far plane to the gel face at 28.3 mm, so it fires up to ~5.6 mm before the gel is pressed. A blind_pick run logged contact true at 28.93 mm while that pad was still short of the face. The flag is a definition of the field, not a tuning, so it holds for every object.
copies:
  - docs/univtac_api_reference.md §3.9
  - univtac_skill/SKILL.md (stopping a descent on contact)
---

## How

It reports whether a surface is inside the pad's range, not whether the gel is
pressed: the flag goes true as soon as the depth drops below the far plane,
with the surface still up to ~5.6 mm short of the gel face (~28.3 mm). Both
flags up means there is a surface in front of each pad, not that the pads are
touching the object.

To check a grasp, read the depths after `close_gripper` — both below the gel
face (see [[grasp-verification]]). To stop a free descent, `move_reflexive`
with `stop_on={"contact": True}` works but fires about 5 mm early; where that
margin matters, descend by commanded position and close there.

## Traps

- Closing on the top millimetres of a block lights both flags and still drops
  it: range is not a grip, and only the depths say whether the pads got past
  the gel face.
- Descending beside an object, with it outside what the pads can see, never
  fires the flag — the pad sees what is between the fingers, not what is next
  to them.
- A pad reading 34.0 mm is at the far plane; that is the same value it reports
  in free air and after dropping the object. Above ~28.3 mm the surface has
  not reached the pad, so the reading alone cannot tell "not touching yet"
  from "no longer touching".
