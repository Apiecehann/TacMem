---
id: A16
name: wrist-limit-segmentation
family: act
summary: Wind the wrist back — release, empty hand, feed_lead_m=0.0, never move_delta — giving back the whole twist still on the wrist, the lead block included, before the next block would take it past a third of a turn; once seated, stop.
task_types: [screw]
channels: []
objects: light_bulb
evidence: Tightening, 120° from one wind-back is proven over 82 twists and twenty grasps with no refusal, and 135° was refused on two independent designs, one of them only 15° into a block. No refusal ever recovered. On 2026-10-06 a live agent spent 180° of one-way twist on a lead search with no wind-back and was refused at 226°. Two later runs the same day both died on the 135 N glass limit during a wind-back — the first left feed_lead_m out of the call and took the planner path instead of the servo; the second got the servo right on every block but rolled the empty hand back with move_delta instead, which turns the arm's position about the world origin and swung it half a metre out of the workspace before the re-grasp. On 2026-10-07 a run turned the bulb 2.83 turns with 5.16 N on the seat — against the 5.19 N the oracle's own passing run finished on — and never saw the end of it, because every block ended in a release and the torque went out with the release; the loop ran on until a wind-back was refused and the episode was over. The same shape returned on 2026-10-08 and this time it was measured — a seated block turned the bulb 20.4 degrees while the release that followed sprang back 19.4, so each cycle bought about one degree, the torque sat at 0.076 of the 0.12 target across twelve of them, and the refusal came anyway. On 2026-10-09 both mistakes ran together for three and a half hours — fifty re-grasps against ninety-two twists, while holding `collapsed and reached` as one exit condition, so no block ever ended its loop. It stood at 2.79 turns and 12.23 mm in with 3.73 N on the seat, still re-grasping, when it was stopped by hand at the wall — the bulb had been home for six re-grasps, and everything spent after the seat bought nothing. The bar was the depth all along (the torque band went on 2026-10-09); what the seat ends now is the run. On 2026-10-10 a run died on the wind-back's direction instead — its blocks all turned at −25° while its own counter held +100, so the empty-hand wind-back it commanded was another −100° — the same way as the blocks, from 133° of twist already on the wrist — and the servo refused it on the second iteration and latched the episode, 1538 s with nothing scored. On 2026-10-11 a count that left the lead block out put a run into the wall — a 15° lead block and four 30° working blocks rode one grasp, its wind-back was scheduled after every fourth *working* block, and the servo refused at 132.45° of un-wound twist — the plan's own 135°. Three runs that passed the same day never carried more than about 91° on a grasp, and the difference between three blocks and four on that grasp was exactly whether the lead block was on it.
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
