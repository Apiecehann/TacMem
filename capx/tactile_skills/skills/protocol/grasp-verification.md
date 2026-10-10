---
id: R9
name: grasp-verification
family: protocol
summary: Verify a grasp on both contact flags up and both depths well below the far plane — and never open before checking.
task_types: [classify, insert, manipulate, screw]
channels: [depth, coincidence]
objects: any
evidence: Implemented in the agent-line warmup tool, which refuses to open on an occupied hand. Marker displacement is routinely one-sided on a real hold; requiring the two pads to agree rejects almost every genuine reading.
copies:
  - univtac_skill/SKILL.md (control workflow, grasp protocol)
  - docs/univtac_api_reference.md §3.9 and §5.1
---

## How

Before closing: read the pads. An empty hand shows the 34 mm far plane with no
contact. If it already reads contact with a depth well below the far plane, the
gripper is *holding something* — find out what before commanding anything.

After closing, the object is held when:

1. `left_contact` and `right_contact` are both true, and
2. both depths are well below 34 mm — a light grasp reads 27-28 mm.

If the fingers closed on air, both pads report the far plane. If the object
slipped out, one or both come back up to it. Re-grasp if the fingers closed
empty.

Before placing: confirm the object is still held, and re-grasp if it is not.

## Traps

- **Do not ask the two pads to agree.** Displacement and shear are commonly
  one-sided even on a firm hold — the object sits nearer one finger, the
  contact patch differs, one pad may be at the edge of the object. Demanding
  symmetry rejects the readings that are actually informative.
- The asymmetry is a *signal* in its own right, not noise: a large, sustained
  depth gap between the pads means the grip is crooked (the pads sit only about
  0.3 mm apart when they are not), and `move_reflexive` can stop on it
  (`stop_on={"asymmetry": 3}`).
- Opening as the first move of a task, on a hand that is already holding
  something, drops it. Confirm the hand is empty first.
- Depth and position disagree exactly when the grasp failed: the commanded
  close reports success while the reading says far plane. Check the reading.
- Contact flags are about the gel being pressed *right now* — see
  [[contact-flag-not-proximity]] for what that does not mean.
- A re-grasp costs a release and a fresh approach, and it is the moment most
  likely to lose the object. Prefer keeping a grasp that is working — see
  [[preserve-existing-grasp]].
