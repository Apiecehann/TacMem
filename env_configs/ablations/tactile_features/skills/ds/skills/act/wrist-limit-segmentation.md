---
id: A16
name: wrist-limit-segmentation
family: act
summary: Wind the wrist back — release, empty hand, feed_lead_m=0.0, never move_delta — giving back the whole
  twist still on the wrist, the lead block included, before the next block would take it past a third of a
  turn; once seated, stop.
task_types:
- screw
channels: []
objects: light_bulb
evidence: Retained original rule A16; unavailable-channel evidence withheld.
---

## How

The budget is the twist you have **not wound back**, not the distance you have
travelled: a ±30° round trip returns the hand to where it started and costs
nothing, while every degree of one-way twisting sits on the wrist until it is
given back. So the count runs over everything — the direction probe, the lead
search, the working blocks — and a search that walks the feed up six times
spends six blocks' worth.

A wind-back is the expensive move, not the twist. It is a release, a re-grasp
and a re-approach, and on 2026-10-09 each one measured about three minutes
against about twenty seconds of turning. The budget above is what one *grasp*
can carry, so let it fill: three 30° blocks in when the lead block rides the same
grasp, four when it does not, then give the wrist back. What is being spent is
the twist still on the wrist, not a count of blocks. A run that winds back after
every single block spends four times as long on the same part.

Keep it at or under a third of a turn, and give it back before spending more.
The wind-back goes in the order the environment's own expert uses:

1. release the part,
2. twist the **empty** hand back to where the segment started — the blocks' own
   angle with the sign flipped, on the same servo the blocks run on and with a
   lead of zero — the object name goes in as usual, an open hand is not a
   reason to reach for another call,
3. re-grasp.

The empty hand matters: turning back while still holding unscrews the part, so
in place is for the probe's small round trips, not for the working blocks.

## Traps

- A refusal is not a stall to push against. It sets a failure flag that only a
  reset clears, so probing for the limit ends the episode there.
- **The seat ends the run, not just the re-setting.** Once the part is seated
  it is in, and that is the whole bar — there is no torque to bring up, nothing
  to hold and nothing to submit. Turning past the seat only winds the wrist on
  for no further depth, and releasing there springs the thread back and takes
  the depth with it: on 2026-10-09 that loop ran for fifty re-grasps after the
  bulb was home, at about three minutes each, until the wrist's own limit ended
  it. The seat is also the one signal that is trusted alone — do not ask it to
  agree with anything before acting on it.
- It cannot be bought back by feeding more slowly or twisting in smaller steps
  — the wrist limits the angle it is holding, and ten small twists reach the
  same limit as one large one. See [[feed-must-match-lead]].
- The wind-back is a call with a lead of **zero**, not a call with the lead
  left out. The two read alike and are different engines; leaving it out hands
  the twist to the task's own expert rotate instead of the servo, which is what
  crushed the glass on 2026-10-06 (see rotate_gripper).
- **It is not move_delta either**, the easier mistake: the hand is empty and
  this call wants an object name, so a plain end-effector rotation looks like a
  substitute. Under the default `coord="world"` a rotation turns the arm's
  *position* about the world origin too, swinging the hand through an arc the
  radius of its distance from that origin — a quarter of a metre per small turn
  from a grasp 0.55 m out.
- Releasing is what [[preserve-existing-grasp]] argues against, and it is still
  right: that rule is about not spending a good grasp for tidiness, and this one
  is about the wrist having run out of turn to give.
- The free direction is not the working direction. A hand turning the thread
  the wrong way is not driving a seat and reaches further before it runs out;
  the 226° above says nothing about how far a tightening turn may go.
- **The wind-back turns the other way** — the segment's own angle with the sign
  flipped, never the negative of a count kept in your own bookkeeping. On
  2026-10-10 a run's blocks all ran at −25° while its counter held +100, so the
  "wind back" it commanded was another −100° — the way the blocks had gone, with
  133° of twist already on the wrist. The servo refused on its second iteration,
  the failure latched for the rest of the episode, and 1538 s ended with nothing
  scored.
