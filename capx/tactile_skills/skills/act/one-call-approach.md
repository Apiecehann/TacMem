---
id: A3
name: one-call-approach
family: act
summary: Reach the grasp pose in one call with z_approach=0.1 — a split approach lands up to a millimetre off.
task_types: [classify, insert, manipulate, screw]
channels: []
objects: any
evidence: Same-seed measurement — the two-call approach lands ~1 mm from the pose, and the following close shoves the object sideways, the same drag that ruins a weight baseline.
copies:
  - docs/univtac_api_reference.md §3.1
---

## How

Pass the grasp pose once, with the approach offset supplied on the call —
`z_approach=0.1` for a grasp or a placement, `0.0` for free-space motion —
rather than issuing "5 cm above" and then the target as two motions. The
planner resolves the approach into the same motion.

## Traps

- A close that lands on a misplaced gripper drags the object laterally through
  the fingers. Everything measured after that close inherits the drag, which is
  why a two-call approach that lands ~1 mm out is worse than it looks.
- The recovery for a contaminated grasp is the same shape: open, re-approach in
  a *single* call, close again. Subtracting the damage afterwards does not work
  — the reading is at the grip's limit, not offset.
