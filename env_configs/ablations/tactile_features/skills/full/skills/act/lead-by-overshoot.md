---
id: A11
name: lead-by-overshoot
family: act
summary: Over-feed one 15° block at 11 mm a turn, read the lead as feed + slide × 360/15 — both in mm, not a search, and that block only. A result at or above the feed, or under 4 mm a turn, is wreckage — never clamp it; re-grasp, do not retry shorter.
task_types: [screw]
channels: [shear]
objects: light_bulb
evidence: On this rig the identity slide = gain × (advance − feed) held to three significant figures across five feeds; a matched feed is the one that leaves the part turning with the fingers instead of lagging them. On 2026-10-07 a run searched instead of reading. It opened a ladder of feeds at 0.4 mm a turn against a thread its own trace shows carrying the bulb at about 4.3, took the first rung's sign for a refusal, and turned the whole episode at a tenth of the pitch. The same day another run read 11 mm a turn as a general test feed and gave it to four direction legs and a retry, where its own trace has the loosening leg sliding 1.1 mm through the gel on 26 degrees of turn. On 2026-10-08 a run's over-fed block slid at the lattice's limit — the thread carried the bulb at 4.3 mm a turn against the probe's 11, so a 30° block asked for 0.56 mm of slide — and the tracker came back with 0.75, which the arithmetic turned into 2.0 mm a turn; every block after it fed the hand 0.17 mm where the bulb went 0.36. On 2026-10-09 a run took its lead probe before the direction was known, and that block happened to turn the loosening way — the true slide there is −1.27 mm, twice past what the lattice follows, and the tracker did not follow it. The run clamped the wrecked arithmetic into a 4 to 11 mm range, which made it the feed, and its first insertion block met the pad guard at 129 N. It ended on a latched refusal, the bulb 2.69 mm further out than it began, with no criteria run at all. Later on 2026-10-09 a second, evening run took three over-fed blocks — 30° and then two at 15°, the shortening this skill asks for — and all three came back under its own gate, the left pad reading 0.04, 0.13 and 0.22 coherence against a right pad at 0.77 to 0.90. No lead was read at all. The run filled the gap with 8 mm a turn of its own, and it began inserting with the pads already at 92 N from the probes. On 2026-10-10 a run met the shallow end with a block that was too long instead — 30°, 30°, 20°, 15° and 10° in one grasp against a thread its trace carries at 4.36 mm a turn, so each 30° block asked for 0.55 mm of slide — the walked lattice gave all five readings back at or above the feed, at 0.94 to 0.98 coherence on the ones it used, and the run filled the gap with 5 mm a turn of its own. Block length is why this skill now asks for 15°, where (11 − lead) × deg / 360 stays between 0.13 and 0.28 mm for every pitch the task draws. On 2026-10-11 a run gated all three of its probes on its own 0.70 and fell through to a written-in 6 mm a turn against a thread its own trace carries at 5.17; this block stays ungated — the gate has one job and it is the seat's reading ([[confidence-gate]]).
---

## How

Two numbers describe one twist — the descent you commanded and the descent the
part actually made — and the gap between them *is* the measured slide:

- **Below the lead**: the part rides along with the fingers, slide near zero.
- **Above it**: the thread will not let the part go faster than its own pitch,
  and the slide turns negative.

So the lead is read off one deliberately over-fed twist, and nothing is
searched for. Command a feed past any lead the bulb could have been given: the
pitch is drawn somewhere between about 4 and 8 mm a turn, so 11 leaves nothing
near it. The thread then holds the glass to its own pitch, and that block's
slide *is* the whole overshoot —

    lead = feed + slide * 360 / block_deg

— with the slide in millimetres, through [[mirrored-shear-slide]]'s gain, and
never in pixels: the arithmetic is meaningless until the shear has been turned
into a length.

The block's length is the lattice's business, not a matter of taste. Its slide
is the whole overshoot — (feed − lead) × deg / 360 — and the tracker follows
only a few tenths of a millimetre of it before the marker jumps
([[lattice-pitch-budget]]). At 11 mm a turn against the shallowest pitch the
task draws, about 4.3, a 30° block asks for 0.56 mm and sits on that limit
before the reading is even taken; at 15° the same thread asks for 0.28 mm, and
the deepest pitch the task draws, about 8, asks for 0.13 — inside the tracker
for every thread there is, and three to four times the block-to-block scatter.
15° is the one length that fits the whole range, so it is the length to take
first.

The feed is already past any pitch the bulb could have been given, so the
arithmetic can only come back **below** it — the block's slide is negative by
construction, the part being unable to outrun the thread. A reading at or above
the feed, or below the floor, is a wrecked reading rather than a lead, and every
block fed on it over-feeds. Neither end is a bound to clamp the answer into, and
neither is a number to move on with: both are the lattice's limit showing
through, and the remedy for both is to take the block shorter, where the same
pitch slides a smaller distance and the arithmetic comes back inside — if the
lattice still has the room. The budget belongs to the grasp, not to the block:
every over-fed degree the grasp has already turned is spent against it, so a
first block that asked for more than the tracker follows leaves the shorter
retries reading its wreckage. On 2026-10-10 that is what the ladder did — five
blocks in one grasp, the shortest of them asking for 0.18 mm, and every one of
them came back at the feed.

Shortening the block answers the lattice's limit and nothing else. A block the
coherence gate rejects is a different failure — the tracker lost the surface,
and no block length brings it back — so the remedy there is another real
reading, not a shorter one ([[confidence-gate]]).

One 15-degree block is enough. From there the feed is the pitch that arithmetic
returned, and the slide goes back to meaning the seat.

The over-feed belongs to that block and to no other twist. It exists to push one
block above the pitch, and the direction legs want about half of it — 6 mm a
turn, over any lead the bulb was given — so that the thread holds the glass back
by a commanded amount and the pair has a slide to read at all: turned in place
the pads read their own scatter and nothing else ([[thread-direction-by-legs]]).
What the legs must not do is spend it more than once. On 2026-10-07 four legs
and a retry ran at the probe's own 11 mm a turn: the loosening leg slid 1.1 mm
over 26 degrees while the hand sank 0.79 mm and the bulb rose 0.30, the tracker
stopped following it, and the widened leg walked the pad force from 122 to 128
N, where the guard aborts the twist ([[lattice-pitch-budget]]). On 2026-10-10
eight legs at 5.5 mm a turn sank the hand 5.4 mm and the pad-force running
maximum went 71 → 127.4 N into the guard — the fault there was eight legs and
four returns, not the feed on them.

## Traps

- Do not servo on the slide block by block: a single block is the pads' worst
  case, scattering nearly 3% of a pitch, enough to walk a damped correction off
  the mark. The pitch belongs to the grasp, not the block — read it from the
  segment's own totals (whole advance over whole angle, ×360) and correct once
  per segment.
- Do not gate the lead reading — or a direction leg — on marker confidence: the
  gate belongs to the seat call alone ([[confidence-gate]] explains why).
- The lead is not a constant of the task — it differs from one run to the next,
  as does the thread's direction, so both have to be measured on the run that
  uses them.
- Do not walk a ladder of feeds looking for the refusal. Below the true lead a
  single block's slide is mostly the pads' own scatter, so its sign is a coin
  toss, and a ladder that opens under the pitch reads a "refusal" on its first
  rung — on 2026-10-07 a run locked the 0.4 mm a turn it had opened with and
  turned the whole episode at a tenth of the pitch. Erring the other way fails
  too: a search that never recognises the refusal does not stop, it ends on the
  largest feed it tried, having watched for a jump in size rather than a sign
  (2026-10-06).
- **A lead above the feed is not a lead.** On 2026-10-08 a run read 0.359 mm on
  the over-fed block, made it 15.311 mm a turn against a pitch the task never
  draws above 8, and fed every block at that pitch. The hand then descended
  three and a half times faster than the thread allowed and the excess went into
  the pads — 94 N of their 128 N guard.
- **A lead under 4 mm a turn is not a lead either.** At this same 11 mm a turn,
  the shallowest thread the task draws (about 4.3) is 0.28 mm of slide in a 15°
  block, inside what the lattice follows; a 30° block on the same thread asks
  for 0.56 mm and sits on the limit before the reading is even taken. A number
  below the floor therefore does not mean a shallow thread: the block went past
  what the lattice follows and the tracker reported the wreckage, and a shorter
  block was the place to start ([[lattice-pitch-budget]]). On 2026-10-09 a run met both ends at once and
  clamped the answer between them — a 4 mm floor and an 11 mm ceiling — so a
  wrecked reading became the feed for every insertion block, two and a half
  times the lead its own trace shows the thread carrying.
- **No thread is deep enough to be unresolvable any more (2026-10-10).** A
  thread within a few tenths of the feed used to sit under the tracker's own
  scatter, where the arithmetic kept coming back at the feed itself and the feed
  was the right answer. The task now draws pitches only up to about 8 mm a turn,
  so at 15° the smallest honest overshoot is 0.13 mm — three to four times that
  scatter, and inside the lattice's budget at the other end of the range. A
  reading at or above the feed is wreckage again, with no depth behind it to
  excuse it.
- **A wrecked block is not rescued by a shorter retry.** The lattice is walked
  by the grasp's whole slide, so once a block has asked for more than the
  tracker follows, the shorter blocks after it read the same wreckage — each
  retry an over-fed turn spent against a budget that is already gone. Size the
  first block for the whole range ([[lattice-pitch-budget]]) instead of planning
  to shorten into an answer.
- An under-fed insertion is not merely slow: it costs half again as many turns
  for the same depth, and every extra turn is another release, another
  re-grasp, another chance to drop the part.
