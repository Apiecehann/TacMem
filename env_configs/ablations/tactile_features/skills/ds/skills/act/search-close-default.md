---
id: A22
name: search-close-default
family: act
summary: Use only the retained observations for this rule; unavailable criteria remain unverified.
task_types: []
tasks:
- blind_pick
channels: []
objects: the hidden block in the blind box
evidence: Filtered from the fixed original skill A22; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

The default close keeps adapting until the fingers meet resistance, so it can
land on a block of unknown width without being told where to stop. It is also
the only probe that catches a block centred between the pads: the descent
leaves it untouched when its faces sit between the fingers, but the close
pinches it. So make the close, then read *whether the call returned* before you
read any depth:

- Over ~1 px on either pad: the block is between the fingers. Deepen or lift.
- Under ~1 px: a graze. One pad's edge pressed the block's side without
  trapping it: the close still returns (27.5-28.0 mm), and it shoves the block
  11-15 mm along the sweep. Do **not** deepen — the servo walks its whole rung
  ladder, raises, and costs about 200 control steps. Open and jump ahead along
  the sweep ([[search-refine-around-contact]]).

That close only *finds* the block. It stops at the first touch, which reads
27-28 mm — 0.3 to 1.3 mm inside the 28.0 mm line the hold is judged against —
and the reading rises as the block settles during the lift. A measured grip
that read 27.55 / 27.39 at first touch read 28.26 / 27.95 a few centimetres
higher, with the block still between the pads, and it was dropped. Deepen,
without opening, only at a site that gripped with either pad still above
~27.5 mm:

A grip that already reads 27.1-27.5 on both pads needs no second close: a
probe grip at 27.460 / 27.134 with 7.26 / 7.72 px held through a 150 mm lift.
Every close costs about 6.5 s of simulated time out of the task's 60 s, so
spend the second close only where the margin is missing.

An open is not free either: it swings the fingers the full range at 0.5 mm per
control step — about 80 steps plus its delay. The cleanup open after a miss
already leaves the gripper open, so opening again at the top of the next
candidate repeats that cost and changes nothing. Open once at the start of the
search; re-open at the top of a candidate only when the previous cleanup open
raised (track it in a variable) or a close left the fingers shut.

An explicit `force_threshold` is also acceptable for the *first* close at a
site already bracketed by a graze — it is servoed and lands on the target —
and it raises on air for the same reason the default does, so it needs the
same try/except. At a site that turns out to be a graze it raises too, which
is the expensive way to learn what the default close says for free.

## Traps
