---
id: A2
name: descent-until-contact
family: act
summary: Descend in small steps and stop when a pad leaves the far plane, not at a fixed depth.
task_types: [classify, insert, screw, wipe]
tasks: [lift_bottle, lift_can, pull_out_key, put_bottle_in_shelf, tactile_transfer_rearrange_clean]
channels: [depth, coincidence]
objects: any
evidence: The motion calls return the same tactile fields a read does, so a stepped descent needs no separate read per step.
copies:
  - univtac_skill/SKILL.md (Stopping on contact)
  - docs/univtac_api_reference.md §3.2
---

## How

```python
while True:
    fb = move_delta(dz=-0.005)
    if fb["left_contact"] or fb["right_contact"]:
        break
```

Every motion returns the tactile fields, so the flag is free. With
`move_reflexive(stop_on={"contact": True})` the same loop runs inside the
server at physics rate — see [[reflex-stop-on-events]].

## Traps

- **Not routed to `blind_pick`.** In a blind search the first pad event marks the
  top edge of an object whose height is unknown, ~25 mm above the depth that can
  hold it, so "stop on the event" is the wrong rule for that task
  ([[search-past-first-contact]]). The other five `manipulate` tasks still get
  this one by name.
- Per-step limits: at most 0.10 m per axis and 0.5 rad, at least 2 mm and
  0.02 rad. A delta below the minimum on *every* axis is rejected outright, so
  a closed-loop descent cannot converge finer than that floor
  ([[small-step-insertion]]).
- When contact is detected, stop pressing. If the depth keeps decreasing, back
  off — something is still moving that should not be.
- "I commanded 5 mm down" is not "the fingers are 5 mm down": the servo landing
  is its own number. Read it back.
