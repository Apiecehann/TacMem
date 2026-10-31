---
id: R9
name: grasp-verification
family: protocol
summary: Grasp — both depths below 28.3, one pad's displacement above ~1 px at close; in transport, only a sustained rise to 28.3+ means it left; never open unchecked.
task_types: [classify, force, insert, manipulate, screw, wipe]
channels: [depth, coincidence]
objects: any
evidence: Implemented in the agent-line warmup tool, which refuses to open on an occupied hand. Marker displacement is routinely one-sided on a real hold; requiring the two pads to agree rejects almost every genuine reading. During a lift the displacement relaxes under a steady grip and can jump on a single frame while the two depths stay put (blind_pick seeds 18 and 20, 2026-10-20). A single depth frame can also leave a narrow band — seed 8 (2026-10-21) held 27.5/27.1 at the close, and its 10 mm lift's returned frame failed its own 24.8-28.3/contact check; that frame is not printed, so the side is unknown.
copies:
  - univtac_skill/SKILL.md (control workflow, grasp protocol)
  - docs/univtac_api_reference.md §3.9 and §5.1
---

## How

Before closing: read the pads. An empty hand shows the 34 mm far plane with no
contact. If a depth is already below the gel face (~28.3 mm), the gripper is
*holding something* — find out what before commanding anything.

After closing, the object is held when both depths are below the gel face
**and one of the pads' marker displacement is above ~1 px** — a light grasp
reads 27-28 mm with a few pixels of marker displacement. Depth alone cannot
verify it: a graze returns 27.8-29 mm with under ~1 px and lights the same
flags as a light grasp. The
contact flags do not verify it either: they go true with the surface still up
to ~5.6 mm short of the pad, so both up is compatible with holding nothing
(see [[contact-flag-is-range]], [[depth-grip-scale]]).

Marker displacement is counted from the reset baseline and is never re-zeroed,
so a pad that has touched nothing still reports a few thousandths of a pixel —
that is the sensor's noise floor, not contact, and it is why the test is a
threshold and not "nonzero". A high value can also be left over from an
earlier touch in the same episode, so the part that belongs to this grasp is
the rise: read the pads before the descent and after the close, and compare.
Under ~1 px on both pads while shut means the object was grazed, not caught.

**Displacement answers the close, not the carry.** Once the object is caught,
the gel relaxes under a steady grip: the marker field drifts back toward zero,
and a single frame can jump and recover. The depths hold through a steady
lift, but a single depth frame can also leave a narrow band and come back
(blind_pick seed 8, 2026-10-21: 27.5/27.1 at the close, and the 10 mm lift's
returned frame failed its own 24.8-28.3/contact check; the frame itself is not
printed). So judge a held object on both depths staying below the gel face;
the loss that matters is sustained — a rise to the gel face (28.3) or beyond
that stays, not one frame. Applying the ~1 px close threshold to every reading of a lift rejects
holds that are still sound (blind_pick seeds 18 and 20, 2026-10-20 — depths
held at 26.5/26.4 through 0.275 m and 27.5/27.2 through 0.20 m while the
displacement decayed; both holds were put back).

If the fingers closed on air, an adaptive close raises "Gripper action
failed" (it cannot reach its depth target) instead of reporting the far
plane; only a position close (adaptive=False) returns the far-plane reading.
If the object slipped out, one or both depths come back up toward it.
Re-grasp if the fingers closed empty.

Before placing: confirm the object is still held, and re-grasp if it is not.

## Traps

- **Do not ask the two pads to agree on displacement or shear.** They are
  commonly one-sided even on a firm hold — the object sits nearer one finger,
  the contact patch differs, one pad may be at the edge of the object.
  Demanding symmetry rejects the readings that are actually informative. The
  depth rule above is different: each pad has its own threshold, and both must
  reach it.
- The asymmetry is a *signal* in its own right, not noise: a large, sustained
  depth gap between the pads means the grip is crooked (the pads sit only about
  0.3 mm apart when they are not), and `move_reflexive` can stop on it
  (`stop_on={"asymmetry": 3}`).
- Opening as the first move of a task, on a hand that is already holding
  something, drops it. Confirm the hand is empty first.
- Depth and position disagree exactly when the grasp failed: a position close
  reports success while the reading says far plane. Check the reading.
- The contact flags are a range reading, not a press — see
  [[contact-flag-is-range]].
- A re-grasp costs a release and a fresh approach, and it is the moment most
  likely to lose the object. Prefer keeping a grasp that is working — see
  [[preserve-existing-grasp]].
