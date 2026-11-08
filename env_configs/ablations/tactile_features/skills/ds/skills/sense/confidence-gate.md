---
id: S19
name: confidence-gate
family: sense
summary: A rejected reading is missing, not a vote — take another, never re-drive the block.
task_types:
- screw
channels:
- coherence
objects: any
evidence: Filtered from the fixed original skill S19; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

Before leaning on a reading whose signal is small, check the coherence that came
back with it. A reading below the gate is not a small reading — it is a reading
of nothing, and it is discarded rather than used.

Discarded means replaced by another real reading — the same block turned
longer, the other pad, a fresh grasp — never by a number of your own, and never
by carrying on as though the reading had agreed. A gate that rejects everything
is not a licence to fall back on a default: it says the tracker was given
nothing to follow, and the answer to that is another measurement, not a fill-in
([[channel-selection]]).

**The gate belongs to the seat's reading, and only to it.** The seat is called
off a slide that is small *for its block* — a tenth of the twist's own commanded
descent, which on a 90° block at a matched feed is around a tenth of a
millimetre, at the size of the 0.08 to 0.11 mm false slides a walked lattice has
produced (the 0.04 mm measured on the day was a seat on a 0.356 mm block, not a
depth to carry to another segment) — so there, and only there, nothing but
coherence separates a seat from a wrecked reading, and 0.70 is the number the environment's
own expert holds that one reading to: it declares a seat only on the reading
that clears it, and its run's candidates sat at 0.21 to 0.60 before one came
back 0.71. Everything else on this task is an order of magnitude louder — a
direction leg's pair differs by twice the lead, 0.6 to 1.9 mm at 30°, and the
over-fed probe's overshoot runs 0.13 to 0.56 mm — and the expert reads both with
no gate at all: its passing runs read the direction legs on a grip whose right
pad sat at 0.24 to 0.31 coherence through the whole probe. Coherence is counted
from the reset-time baseline rather than from the last reading, so a pad that
has not slid much since the reset reads low whatever the tracker is doing. Use
the gate where the signal is small; take the number you got where it is not
([[thread-direction-by-legs]]).

A close is compression, and compression reads low by definition: gating a grasp
on coherence asks a squeeze to look like a slide, so it never passes. Whether
the hand holds the object is a contacts-and-depths question
([[grasp-verification]]).

## Traps

- **The failure mode is silence, not noise.** A tracker that has lost the
  pattern still returns a magnitude in the normal range; nothing about the
  number itself says it is untrustworthy. That is why the gate has to be in the
  criterion rather than in the reader's judgement — and it is a narrow tool all
  the same: on 2026-10-10 a walked lattice read 0.94 to 0.98 coherence on every
  wrecked reading, so clearing the gate says the tracker followed the markers,
  never that the markers were showing the move you asked for
  ([[lattice-pitch-budget]]).
- **The gate belongs to the seat call, not to a leg or a slope.** Coherence
  collapses for a grasp or two after every re-grasp and much of a normal
  insertion runs under 0.5, so a coherent-only lead slope collects no degrees at
  all ([[lead-by-overshoot]]) and a gated pair of direction legs decides
  nothing: on 2026-10-10 a run gated both legs at 0.70, threw twenty degrees of
  reading away on a grip whose pads were a pixel and a half off their baseline,
  and ran the episode on a direction it wrote in ([[thread-direction-by-legs]]).
  The same collapse is why a close reads under the gate almost every time: it
  is the one moment in the task that is pure compression, and a gate on it fails
  a grasp that is physically fine.
- **A gate is not a stop condition.** A reading the gate rejects is a missing
  measurement, so the move is to take a better one — close again, turn longer,
  read the other pad — never to end the episode on it. On 2026-10-08 a run
  raised on the first gated grasp and lost the whole episode with the bulb
  still in the socket.
- **A rejected reading does not un-drive the move that produced it.** The block
  has already turned and descended whatever it turned and descended; a missing
  reading for it is a missing reading, not a reason to run it again. Re-driving
  is how a missing number becomes two blocks of travel: on 2026-10-10 a run's
  left pad read 0.26 to 0.32 through a fresh grasp, so its loop re-ran the same
  fed block twice more — 60 further degrees of turn, 0.83 mm further into the
  socket — until the servo's own limit refused the third and latched, with the
  bulb 2.67 mm in and no criterion ever reached. The block counts as run; the
  reading is taken from what comes next ([[regrasp-after-slip]]).
- A gate without a fallback just throws the reading away. Decide in advance
  what a failed gate means — re-take the reading, or fall back to a different
  *real* signal — never to a number you supply yourself. A discarded reading is
  *missing*, and a missing reading has no vote: the zero a helper writes in its
  place is not a measurement of zero. On 2026-10-07 a live run gated both
  thread-direction legs, substituted 0.0 for each, and read the two as
  *agreeing* — the widening that followed was ordered by the substitution, not
  by the thread, and the direction was then decided by a tie-break over two
  zeros. On 2026-10-09 an evening run met the same gate on every measurement it
  took and filled both gaps — an 8 mm a turn lead and a +1 direction of its own
  — then turned the whole insertion the way that unscrews the bulb, 154 degrees
  of it, until the pads crushed the glass at 136 N.
- Repeated failures of the gate on the same object are information about the
  surface, not about the gate: a transparent or featureless surface gives the
  tracker nothing to follow ([[channel-selection]]).
