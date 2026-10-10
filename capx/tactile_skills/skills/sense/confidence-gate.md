---
id: S19
name: confidence-gate
family: sense
summary: Gate the slide you measure on coherence at or above 0.70 — a close is compression and reads low, so never gate a grasp on it. A rejected reading is missing, not a vote — re-take it, never make one up.
task_types: [classify, insert, manipulate, screw]
channels: [coherence]
objects: any
evidence: Coherence is the uniformity of marker motion, high when the markers move together and low when they move incoherently or the tracker has lost them; the reflex and slip conditions both require 0.7 before believing a displacement. On 2026-10-07 a run whose gate rejected both thread-direction legs wrote 0.0 into each, read the pair as agreeing, and widened the legs; its tie-break then picked a side off two zeros. On 2026-10-08 a run gated its grasp check the same way — the close that held the bulb at a normal 27-28 mm depth and 89 N of pad force came back below the gate, and it threw the episode away at the first grasp without checking anything else. On 2026-10-09 an evening run had the left pad read 0.09 coherence at the grasp and 0.04, 0.13 and 0.22 after its three lead probes — under the gate every time, while the right pad read 0.62 at the grasp and 0.77 to 0.90 after — so it fell through to numbers of its own, an 8 mm a turn lead and a +1 direction, turned 154 degrees the way that unscrews the bulb, and crushed the glass at 136 N of pad force.
---

## How

Before using a slide you intend to measure or compare, check the coherence that
came back with it. A reading below the gate is not a small reading — it is a
reading of nothing, and it is discarded rather than used.

Discarded means replaced by another real reading — the same block turned
longer, the other pad, a fresh grasp — never by a number of your own, and never
by carrying on as though the reading had agreed. A gate that rejects everything
is not a licence to fall back on a default: it says the tracker was given
nothing to follow, and the answer to that is another measurement, not a fill-in
([[channel-selection]]).

The gate is for the shearing readings, the ones whose sign or size you are
about to use. A close is compression, and compression reads low by definition:
gating a grasp on coherence asks a squeeze to look like a slide, so it never
passes. Whether the hand holds the object is a contacts-and-depths question
([[grasp-verification]]).

## Traps

- **The failure mode is silence, not noise.** A tracker that has lost the
  pattern still returns a magnitude in the normal range; nothing about the
  number itself says it is untrustworthy. This is why the gate has to be in the
  criterion rather than in the reader's judgement — and why an unlocked lattice
  is caught by the gate and by nothing else.
- **The gate belongs to the seat call, not to the lead.** Coherence collapses
  for a grasp or two after every re-grasp and much of a normal insertion runs
  under 0.5, so a coherent-only lead slope collects no degrees at all
  ([[lead-by-overshoot]]). The same collapse is why a close reads under the
  gate almost every time: it is the one moment in the task that is pure
  compression, and a gate on it fails a grasp that is physically fine.
- **A gate is not a stop condition.** A reading the gate rejects is a missing
  measurement, so the move is to take a better one — close again, turn longer,
  read the other pad — never to end the episode on it. On 2026-10-08 a run
  raised on the first gated grasp and lost the whole episode with the bulb
  still in the socket.
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
