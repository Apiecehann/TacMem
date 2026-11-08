---
id: A9
name: preserve-existing-grasp
family: act
summary: A working grasp beats a tidier one — every release risks losing the object.
task_types: [manipulate, insert, screw, wipe]
channels: []
objects: any
evidence: On the screw task each re-grasp is also a released part and a thread to find again, which is why the reset cadence is planned around the wrist limit rather than around convenience.
---

## How

Re-grasp only when something says the grasp has failed — a verification reading
that says so ([[grasp-verification]]), a slip, or a task step the current grasp
cannot reach. Otherwise carry on with what is already held.

A grip can also fail as an *instrument* while passing every contact check: both
flags up, depths normal, and one pad's marker field sitting a lattice off its
no-load baseline, which is a pad that cannot answer any reading you take from
it. That is a failure reading too — the difference the task needs cannot be
measured on it — and it is remade, not preserved. Note the direction it does
not cover: a low coherence on its own is what a close looks like and is not a
failure ([[confidence-gate]]).

## Traps

- Re-grasping "to be safe" trades a known-good grasp for an unknown one. The
  close that follows a re-approach can land off-centre, and the approach itself
  can knock the object over.
- The same goes for letting go: opening the gripper is a placement decision,
  and if the task does not need the object released, leaving it held is the
  safer end state.
