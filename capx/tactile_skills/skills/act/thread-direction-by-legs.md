---
id: A10
name: thread-direction-by-legs
family: act
summary: Two legs, one each way, 20° in place (feed 0) — read each leg's own change; the higher signed slide tightens. Closer than 0.15 mm, widen to 45° once; still together, take a side and let the first block settle it. A rejected leg is missing, not a zero — re-take it, never invent one.
task_types: [screw]
channels: [shear]
objects: light_bulb
evidence: At a few degrees the two legs separate by one or two pixels, under the tracker's own scatter — the same grasp has read them on both sides of zero over repeated runs. Long legs separate by more than the ~0.15 mm resolution. On 2026-10-06 a live agent ran the two 30° legs and still turned the loosening way, spending 226° of wrist to back the bulb out 2.5 mm. On 2026-10-07 a run fed both legs at the lead probe's own 11 mm a turn — the loosening leg dragged the pads 1.1 mm over 26 degrees, every reading fell to its gate, and the direction was picked off two substituted zeros. On 2026-10-08 a run gated one leg, wrote 0.0 in its place, and let that zero win the comparison against the other leg's real, negative reading — 130° of wrist went the loosening way with the hand feeding down, the pad-force running maximum climbed from 71 N after the close to the server's 128 N guard, and the episode ended there, 1.39 mm further out than it began. Later the same day a run compared two legs turned the same way and called their agreement a direction — three such legs all went the loosening way, 90° of wrist and 0.9 mm of thread spent before it tried the opposite pair. On 2026-10-09 a run compared the two legs by size rather than signed — 0.321 mm against 0.019, both at the same 5.5 mm a turn — took the larger for the tightening side, and spent the whole episode turning loosening. It backed the bulb out 2.69 mm over 225° of wrist, drove its first insertion block into the 128 N pad guard, and ended on the first refusal with no criteria run at all. Later on 2026-10-09 a second, evening run never turned a loosening leg at all — its own loop abandoned the pair when the gate rejected the +30° leg, both +45° retries refused and latched the task failure at screw_IK_limit, and a hard-coded +1 took the direction. The sixty screw calls after it raised one by one, each having already turned the part a servo iteration, and the run ended with the bulb 0.44 turns and 1.7 mm out of the socket and the glass crushed under a 136 N pad force. On 2026-10-10 a run followed the recipe this skill carried — two legs at half the probe's over-feed, widened to 45° when the pair came back close — and compared each leg's raw shear instead of its own change — the fields count from the reset baseline, both legs had accumulated the hand's 5.4 mm of sink, and six legs under the 0.15 mm gate separated nothing. The widening was unbounded and the feed was charged to the pads on every leg, so the pad-force running maximum climbed 71 → 102 → 127.4 N and the ninth twist ended on the server's 128 N guard.
---

## How

At a few degrees the pads are reading their own scatter, so the direction needs
legs an order of magnitude longer than that. Twist ±20° **in place** — the leg
calls carry `feed_lead_m=0.0`, never the insertion feed — and read each leg
across its **outbound** turn alone, before it returns home. Widened to 45° once,
if the pair still comes back closer than the 0.15 mm below. Both legs ask the
same of the pads and turn the same angle; they differ in one respect only —
which way the thread drives the part — and the pads separate them.

**Read the slide signed, never its size.** The leg the pads read higher is the
one that tightens — but "higher" is a claim about the signed slide, the
left-minus-right shear over the gain ([[mirrored-shear-slide]]), not about how
far the markers moved. The two legs come back as lead − feed and −(lead + feed)
a turn, so they differ by twice the lead whatever they are fed; at the zero feed
they are turned at, that is +lead and −lead — a symmetric pair, each leg half
the separation and both inside the lattice's budget. A comparison on the sizes
inverts the answer every time, and off the symmetric pair it does so quietly:
the raw sizes can sit well apart, past the ~0.15 mm the readings are trusted to,
so the wrong answer never shows up as a close call.

**And read each leg as the change it made.** The shear fields are counted from
the reset baseline, so the number a twist returns is the episode's total and not
that twist's own slide ([[mirrored-shear-slide]]). Take one reading before the
leg and one after it, and subtract; without the subtraction both legs report the
same accumulated sink.

Believe the answer only when the two changes separate by more than about 0.15
mm. Below that, widen once to 45° — and if that pair is still together, it is
the channel that is broken rather than the thread: take a side deliberately, say
so, and let the first insertion block's own slide settle it, because a wrong
side backs the part out and the block reads it.

**Why the legs get no feed at all.** The feed is not what separates them: they
differ by twice the lead per turn whatever they are fed, so a feed buys the
comparison nothing and costs two things. It costs the lattice — the feed is
added to the out leg's slide, which goes at feed + lead a turn, and taken off
the in leg's — and on 2026-10-07 four legs and a retry at 11 mm a turn slid the
loosening leg 1.1 mm over 26 degrees, past what the marker lattice follows, so
that run's gate rejected every reading ([[lattice-pitch-budget]]). And it costs
the pads: both legs sink the hand by the feed whatever the sign — the servo
winds the descent on the angle's magnitude, not its direction — and no leg
brings the hand back, so every pair buries it twice the feed's travel deeper in
the glass. On 2026-10-10 a run turned eight legs at half the probe's over-feed,
5.5 mm a turn against this bulb's 4.3: the hand sank 5.4 mm, the pad-force
running maximum climbed 71 → 102 → 127.4 N, and the ninth twist ended on the
server's 128 N guard. In place, a leg costs the wrist and nothing else, which is
what makes widening affordable again.

**There is no default.** A leg the coherence gate rejects is not a leg — it is
a missing measurement, and the answer to a missing measurement is a longer one,
never a coin flip between the two directions. Nor is it a zero, and here the
difference is the whole decision. The in leg reads lead − feed a turn and the
out leg −(lead + feed), so the out leg is the lower of the two whatever the feed
and whatever the pitch. A helper that writes 0.0 in a rejected leg's place is
therefore not filling in a neutral value — 0.0 sits above the out leg's reading
every time, so it is casting a vote for the loosening side, and casting it
against a reading that is real. Note which leg
is likeliest to be the rejected one: at a feed the out leg slides furthest, past
what the marker lattice follows, so the gate tends to remove exactly the leg
whose reading would have been the loudest; in place the two slide alike, and the
gate rejects whichever pad has lost the surface. Missing either way, never loud.

The default can hide in the code rather than in the gate. A rejected leg is
enough to end the pair before the other leg is ever turned, and whatever sits
after the loop — a hard-coded +1, a note about right-hand threads — is then the
whole decision, taken without the thread ever being read. On 2026-10-09 an
evening run never turned a loosening leg at all, and its +1 was the direction
the bulb came out on.

The cost of guessing wrong is not a wasted block: the whole run goes the wrong
way, spends its wrist budget backing the part out, and ends on the first
refusal (2026-10-06). On 2026-10-08 it ended on the pad guard instead — a
substituted zero took the direction, 130° of loosening followed with the hand
feeding down while the thread lifted the bulb, the pad-force running maximum
climbed from 71 N to the 128 N the server allows, and the call raised rather
than come back. Turning further in place is cheap by comparison — it spends
wrist, not pads — and the widened pair is what ends with an answer. When even
45° leaves the two together, the channel is the broken part, and the first
block's own sign is a better witness than a third width; it costs one block.

## Traps

- A feed on a leg call is not a neutral choice: it sinks the hand by its own
  travel and nothing brings it back, so the pads pay for a comparison the feed
  does nothing to sharpen. In place, a reading past a few tenths of a
  millimetre means the leg was long — shorten it, never widen it again
  ([[lattice-pitch-budget]]).
- The residual of a round trip is mostly hysteresis rather than signal, so a
  return leg is not a second opinion.
- Direction follows the thread's handedness, and like the lead it is a per-run
  quantity to be measured on the run that uses it ([[lead-by-overshoot]]).
