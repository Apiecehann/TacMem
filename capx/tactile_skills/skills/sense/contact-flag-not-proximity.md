---
id: S16
name: contact-flag-not-proximity
family: sense
summary: Descend by commanded position — the contact flag reports a press, so it never stops a free descent.
task_types: [classify, insert, manipulate, screw]
channels: [depth, coincidence]
objects: any
evidence: An open gripper descending beside an object returns False the whole way; the flag turns True only once the close has pinched something. The flag is a definition of the field, not a tuning, so it holds for every object.
copies:
  - docs/univtac_api_reference.md §3.10
  - univtac_skill/SKILL.md (stopping on contact)
---

## How

It answers "is the grasp engaged", not "am I there yet": drive
`close_gripper`, then read the flags before lifting. Both up means the object
is between the fingers.

To find the object while descending, drive by **target position** — small
`move_delta` steps, or `move_reflexive` with `stop_on={"contact": True}`, which
evaluates the flag inside the motion rather than between calls. The stop fires
on the press that follows the close, not on the approach.

## Traps

- Descending on an open gripper beside an object, expecting the flag to fire
  when the fingers draw level, waits forever. Nothing is pressing the gel.
- A pad reading 34.0 mm is at the far plane; that is the same value it reports
  in free air and the same value it reports after dropping the object. The
  reading alone cannot tell "not touching yet" from "no longer touching".
