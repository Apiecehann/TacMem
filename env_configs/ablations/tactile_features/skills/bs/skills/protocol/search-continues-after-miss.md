---
id: R14
name: search-continues-after-miss
family: protocol
summary: A missed grasp or a refused motion — the retract too — costs one candidate, not the episode; catch each, open, and try the next site.
task_types: []
tasks: [blind_pick, blind_pick_balls]
channels: [depth]
objects: the hidden block in the blind box
evidence: In the seed 3-10 batch (2026-10-19), seeds 5, 6, 9 and 10 raised RuntimeError at the first lost grasp and ended the trial; seed 10's own x=0.544 column was never visited. Both passing seeds moved on from a failed site — seed 2's depth check rejected (0.520, 0) and the block held at (0.496, 0); seed 1's empty close at (0.520, -0.052) raised, was caught, and the next site held. The placement is x = 0.520 +/- 0.028, y = 0 +/- 0.052 (ViTaForge envs/_blind_box.py), so a miss leaves several columns untried. The gpt-6-astra seed-4 run (2026-10-19) died on a motion call rather than a grasp — an uncaught planner refusal during a routine 61 mm upward retraction from (0.520, +0.040) ended the episode at site 11 of 35, before (0.548, 0) — 3.4 mm from the block's true centre — was reached. Its 2026-10-19 rerun repeated the ending exactly — the site's cruise was refused, the recovery retract was refused too, and that retract — issued outside the try — ended the episode after 11 sites. A same-pose move probe (2026-10-19) was accepted, so the zero-length plan is not what the planner rejects. Routed to blind_pick_balls on 2026-10-24 (gpt-6-astra, seed 1, first LLM run on that task) — the program ended at its first row on an uncaught `goto_pose` refusal, the arm coming back 16 mm short (13 mm east, 9.7 mm high) of a descent to ball-centre height at x=0.365 — in that task too a refused motion is one candidate failing, and each ball the sweep stops at is one candidate.
---

## How

The episode is a search over candidate columns, and one close tests only one
column. After a miss:

1. Lift first and read both pads. If both are at the far plane (34.00), the
   block is not in the grip, so opening is safe.
2. Open, rise back to the search height, move to the next column, descend to
   wrist ~0.177 ([[search-past-first-contact]]), and close again
   ([[search-close-default]]).

The same rule runs along a row in `blind_pick_balls`, where the refusals do
not come from a planner: the box has no planner and the hand is driven by a
differential IK, so a pose the arm cannot hold is refused by the arm itself —
the TCP comes back short (16 mm on the 2026-10-24 run) and the call reports
failure after the whole per-move step budget. Each ball the sweep stops at is
one candidate; a refusal at one of them costs that ball, not the run.

A motion call that fails — a planner rejection, or an adaptive close that
raises on air — is that candidate failing, not the episode. The try/except
must wrap the motion calls too, not only the close. **The retract is a motion
call too**: it is the last thing the loop does, so an unguarded refusal there
ends the run after all the other work has succeeded:

```python
for x, y in sites:
    try:
        goto_pose([x, y, CRUISE_Z], DOWN)
        goto_pose([x, y, SEARCH_Z], DOWN)
        ...                     # close, verify, lift
    except RuntimeError:
        pass                    # this candidate failed, not the episode
    # Empty hand (both pads at 34.00): open and move on. Holding something:
    # return to the search height first, never open in midair
    # ([[grasp-verification]]).
    try:
        open_gripper()
        goto_pose([x, y, CRUISE_Z], DOWN)
    except RuntimeError:
        pass                    # an arm already at the search height needs none
```

End only when the candidate list is exhausted or the run's own alarm has
fired. `check_success` is evaluated automatically at the end, so there is
nothing to gain by raising early.

## Traps

- **Do not raise on the first lost grasp.** Seeds 5, 6, 9 and 10 did, each
  after trying only one or two columns of their own list; seed 10 stopped at
  (0.520, -0.04) with its best column, x=0.544, still unvisited.
- Opening after a lost grip is safe only once the far plane is confirmed. If
  the depths are still below the gel face the block may be held: keep the grip
  and finish the lift step before deciding ([[grasp-verification]],
  [[preserve-existing-grasp]]).
- A blank column is not a broken gripper. The adaptive close raises when the
  pads meet nothing; catch it, open, and carry on
  ([[search-close-default]]).
- **A planner refusal is routine, not fatal.** In the gpt-6-astra seed-4 run
  (2026-10-19) the episode ended on `Motion planning failed` during an
  ordinary 61 mm upward move with an empty hand — at site 11 of 35, with the
  most useful column still untried. One refused motion says the planner could
  not solve that step; it says nothing about the next candidate.
- **The retract is the motion most likely to be refused.** The 2026-10-19
  rerun of seed 4 died the same way: the cruise (or the descent) at a site
  was refused, and the recovery retract was refused too, so the second
  `Motion planning failed` was the one that ended the run. A refusal during
  an ordinary retract is not a special case — the planner refuses ordinary
  moves intermittently — so guard the retract like any other motion and let
  the loop move on. (A move to the pose the arm already occupies is
  accepted, so it is not the zero-length plan that fails.)
