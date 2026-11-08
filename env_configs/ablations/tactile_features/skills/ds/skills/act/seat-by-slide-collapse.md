---
id: A13
name: seat-by-slide-collapse
family: act
summary: A bottomed part takes none of the block — the seat is the slide going negative, past a tenth of that
  block's descent in mm, and only once your twists have sunk the hand 5.5 mm, on a feed that matches the thread,
  and on a reading at coherence 0.70 — fed over it, every block clears that tenth. Once it reads, no wrist
  resets — press on until the API reports seated.
task_types:
- screw
channels:
- shear
- coincidence
objects: light_bulb
evidence: Retained original rule A13; unavailable-channel evidence withheld.
---

## How

While the thread is taking the part in, the hand and the glass descend together
and the slide stays small. Once the part is seated the hand goes on down and
the glass does not, so the slide collapses toward the *whole commanded descent*
of that twist, running the other way from the thread's.

Read it as a **fraction of the twist's own commanded descent, on the side that
means the glass took none of the block** — a tenth of it or more is the seat.
The tenth is a floor, never a ceiling: a correctly fed block slides a few
percent of its descent, so a rule that calls the seat on a *small* slide calls
it on every quiet block there is. Never a fixed depth either — the seat's slide
scales with how far the block was asked to go, so a threshold in millimetres
moves with the segment length, and 0.04 mm (measured on a 0.356 mm block) is a
quiet block on any longer one.

## Traps

- **Magnitude alone cannot make this call.** A tracker that has lost its
  lattice reports an unrelated number at the same plausible size, so a slide
  that looks like a seat may be a lost marker. Hold the seat's reading to
  coherence ≥ 0.70.
- **Arm the call on the hand's own descent, not on the reading.** A part shallow
  in its socket has not reached a seat at all, and a block fed *over* the
  thread's lead slides the same way and can pass the same tenth — the lead
  probe, the direction legs, and any block fed on a lead measured long all read
  like a seat. What separates them from it is depth: arm only once the twists
  have sunk the hand 5.5 mm of its own commanded descent (feed × angle, summed
  over every twist since the reset — the hand is commanded, so that count is
  exact). Counting the legs in makes this a later arm than the environment's own
  expert, which arms the same 5.5 mm of a count that starts after its legs, and
  whose comment says why the arm has to clear the whole of an over-fed grasp. A
  fixed depth threshold called a seat 3 mm in and cost that run; so did the
  ceiling read, at 3.85 mm of a 6.5 mm travel.
- **Both halves of the call are the feed's, so a wrong feed makes it wrong
  twice over.** The tenth asks the slide to be a tenth of the twist's descent,
  and that fraction is `(lead - feed) / feed`, so a feed an eighth over the
  thread clears the tenth on *every* block by construction rather than because
  the part has bottomed; and the arm counts the hand, which on a long feed sinks
  its 5.5 mm while the part is still short. On the 2026-10-11 run a written-in
  8 mm a turn against a 4.24 mm thread would have passed the tenth from its
  first block and had the arm up during its sixth — that run's two direction
  legs cancel each other's descent, but its four probes alone sink the hand
  1.85 mm — with the bulb 2.8 mm into its 6.5 mm travel, a seat called at less
  than half depth, which is what the arm exists to prevent.
  A feed that failed to measure is therefore a false-seat generator, and the
  remedy is to measure it again ([[lead-by-overshoot]]); never lean on this call
  while feeding on a number written in ([[feed-must-match-lead]]).
- **A call that never comes is a reading to diagnose, not a run to
  continue.** The seat's slide is negative because a bottomed hand outruns the
  glass, so a channel that has never read negative has not been shown a seat —
  and a hand held *above* the glass, fed on a lead measured long or fed with
  the sign that lifts it, reads positive however deep the bulb is, the seat
  included. On 2026-10-11 a run fed all fifteen of its blocks that way — a
  negative lead on a negative twist, so the servo raised the hand 0.67 mm a
  block — and its seat rule stayed silent the whole way; the API's `reached`
  ended the run instead, on a bulb that was genuinely home. When the call has
  not come by the time the hand has sunk its 5.5 mm, check the feed's sign
  before spending more travel.
- **The call ends the twisting, not the press.** A seat read says the bulb has
  stopped taking the block, not that it is loaded. On 2026-10-11 the bulb went
  home at 2.5 N of seat force and the run's own three-block cadence made its
  wrist reset anyway — the open gripper dropped the load to 0.45 N and the same
  press had to be bought again with a whole further block. Once this call reads,
  stop resetting and keep the fed blocks coming: the gel sees the bulb stop,
  never how hard it is pressed, so the load the scorer wants is the API's
  `reached` to report — the state the task is scored on — and this line's job is
  only to stop spending twist on it ([[wrist-limit-segmentation]]).
- **Confidence is for this call alone.** The lead probe and the direction legs
  are deliberately ungated — their signals are an order of magnitude larger than
  this one ([[confidence-gate]]).
- Both numbers here are this task's — 6.5 mm of free travel, and 5.5 mm of hand
  before the call is armed. Travel is task geometry, not a constant of the
  method: re-read it against the object rather than trusting the numbers above.
