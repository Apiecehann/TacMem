---
id: A10
name: thread-direction-by-legs
family: act
summary: Two legs, one each way, 30° at about 6 mm a turn, each read on its outbound turn; the higher signed slide tightens — that leg barely slid, so it reads low coherence; take it anyway. Closer than 0.15 mm, widen once to 45°; a pad a lattice off its baseline is lost — re-grasp, re-run.
task_types: [screw]
channels: [shear]
objects: light_bulb
evidence: At a few degrees the two legs separate by one or two pixels, under the tracker's own scatter — the same grasp has read them on both sides of zero over repeated runs. Long legs separate by more than the ~0.15 mm resolution. On 2026-10-06 a live agent ran the two 30° legs and still turned the loosening way, spending 226° of wrist to back the bulb out 2.5 mm. On 2026-10-07 a run fed both legs at the lead probe's own 11 mm a turn — the loosening leg dragged the pads 1.1 mm over 26 degrees, every reading fell to its gate, and the direction was picked off two substituted zeros. On 2026-10-08 a run gated one leg, wrote 0.0 in its place, and let that zero win the comparison against the other leg's real, negative reading — 130° of wrist went the loosening way with the hand feeding down, the pad-force running maximum climbed from 71 N after the close to the server's 128 N guard, and the episode ended there, 1.39 mm further out than it began. Later the same day a run compared two legs turned the same way and called their agreement a direction — three such legs all went the loosening way, 90° of wrist and 0.9 mm of thread spent before it tried the opposite pair. On 2026-10-09 a run compared the two legs by size rather than signed — 0.321 mm against 0.019, both at the same 5.5 mm a turn — took the larger for the tightening side, and spent the whole episode turning loosening. It backed the bulb out 2.69 mm over 225° of wrist, drove its first insertion block into the 128 N pad guard, and ended on the first refusal with no criteria run at all. Later on 2026-10-09 a second, evening run never turned a loosening leg at all — its own loop abandoned the pair when the gate rejected the +30° leg, both +45° retries refused and latched the task failure at screw_IK_limit, and a hard-coded +1 took the direction. The sixty screw calls after it raised one by one, each having already turned the part a servo iteration, and the run ended with the bulb 0.44 turns and 1.7 mm out of the socket and the glass crushed under a 136 N pad force. On 2026-10-10 a run followed the recipe this skill carried — two legs at half the probe's over-feed, widened to 45° when the pair came back close — and compared each leg's raw shear instead of its own change — the fields count from the reset baseline, both legs had accumulated the hand's 5.4 mm of sink, and six legs under the 0.15 mm gate separated nothing. The widening was unbounded and the feed was charged to the pads on every leg, so the pad-force running maximum climbed 71 → 102 → 127.4 N and the ninth twist ended on the server's 128 N guard. On 2026-10-10, the evening run, the close left one pad 22.8 px off its no-load baseline with a coherence of 0.10, and it stayed between 0.09 and 0.30 through all twelve legs and the three probe blocks — every leg of every pair was rejected, the differential had that pad's mess on one side of it, and the code fell through its ladder to a written-in +1 — the run spent 1044° of wrist and 2.90 turns of thread the loosening way, backed the bulb 10.2 mm out of its socket, crushed the glass under a 139 N pad force, and ended on a re-grasp it could not reach, nothing scored. Four blocks into the main loop the same grasp's own re-grasp brought that pad back to 0.976. On 2026-10-13 a run gated every reading at 0.70 and threw the tightening leg out of all four of its 30° pairs — the loosening legs came back at 5.2 to 7.1 px with 0.95 to 0.97 coherence, the tightening legs at 2.0 to 2.2 px with 0.28 to 0.63, and the discarded reading did not change over the eight re-takes the run spent on it — so no pair ever compared, the widening was never reached, and the episode ended with its whole 2622 s spent and the direction unread. The server's own step trace of that run has the two sides the way this skill says — the +30° legs backed the bulb 0.346 mm out and the −30° legs drove it 0.367 mm in. Against all of that, the environment's own expert turns its two legs ±30° at 6.15 mm a turn and reads the pair with no gate — on 2026-10-05 it read −13.5 against −2.9 px and −19.2 against −1.5 px on two runs — 0.59 and 0.98 mm apart — on a right pad whose coherence sat at 0.24 to 0.31 through the whole probe, turned the higher leg each time, and finished both runs SUCCESS. That is the recipe this skill carries.
---

## How

At a few degrees the pads are reading their own scatter, so the direction needs
legs an order of magnitude longer than that. Twist ±30° at **about 6 mm a
turn** — half the lead probe's 11, the environment's own expert's leg feed, and
a number that needs no lead to pick — and read each leg across its **outbound**
turn alone, before it returns home. Widened to 45° once, if the pair still comes
back closer than the 0.15 mm below. Both legs ask the same of the pads and turn
the same angle; they differ in one respect only — which way the thread drives
the part — and the pads separate them. Both legs and both returns sink the hand
by |angle| × feed whatever the sign, so the pair costs about 2 mm of hand
descent: one pair and one widening are the whole of it.

**Read the slide signed, never its size.** The leg the pads read higher is the
one that tightens — but "higher" is a claim about the signed slide, the
left-minus-right shear over the gain ([[mirrored-shear-slide]]), not about how
far the markers moved. The two legs come back as lead − feed and −(lead + feed)
a turn, so they differ by twice the lead whatever they are fed — at a feed past
the pitch both sit on the negative side, the loosening leg hard negative and the
tightening leg only a little. A comparison on the sizes inverts the answer every
time, and it does so quietly: the raw sizes can sit well apart, past the
~0.15 mm the readings are trusted to, so the wrong answer never shows up as a
close call.

**The leg that answers reads low coherence, and that is arithmetic rather than
damage.** Coherence is the patch's common motion over its motion overall, so a
leg that barely slid divides a small signal by a small total — the tightening
legs of the 2026-10-13 run read 0.28 to 0.63 while the loosening legs that
dragged the pads read 0.95 to 0.97. A gate at `detect_slip`'s own 0.70 therefore
keeps the loosening leg of every pair and throws the one that carries the answer
([[confidence-gate]]).

**And read each leg as the change it made.** The shear fields are counted from
the reset baseline, so the number a twist returns is the episode's total and not
that twist's own slide ([[mirrored-shear-slide]]). Take one reading before the
leg and one after it, and subtract; without the subtraction both legs report the
same accumulated sink.

**And run the pair on a grip that can answer.** The comparison needs two
readings, one from each pad, so one pad that has lost its surface takes the
whole pair with it — and the close is where a pad gets lost. On 2026-10-10 the
evening run's close dragged one pad 22.8 px (a lattice and a bit) off its
no-load baseline with a coherence of 0.10; it read between 0.09 and 0.30 for
the rest of the probe phase. Every leg the run turned came back rejected, and
the gate was right to reject them: a field that far from its reference is not
tracking anything. Re-taking the leg is no help — it re-reads the same pad.
What fixes it is a new grip: opening the gripper unloads the pads and the
markers relax back toward their reference, which is the fact the environment's
own probe leans on when it backs off between its steps. The same grasp's
re-grasp, four blocks later in that run, had that pad tracking again at 0.976. So read
both pads before the pair, and treat a pad already a lattice (~20 px) off its
baseline as a lost surface: re-grasp first, then run the pair. Every healthy
post-close reading on this task has been within a few px of its reference — two
runs read 1.6 px and 1.7, then 1.6 and 1.4 — against 22.8 and 33.9 on the two
dead ones. A pad the gate keeps rejecting while the other one reads fine is
the same symptom arriving one probe later ([[regrasp-after-slip]],
[[confidence-gate]]). One re-grasp per loss, and check the new grip before
spending legs on it; a fresh grip whose pads are both a lattice out, or both
under the gate through the pair, is the surface rather than the grip — then the
pair will not answer however many times it is remade, and that is a fault to
report, not a reason to guess.

Believe the answer only when the two changes separate by more than about 0.15
mm. Below that, widen once to 45° — and if that pair is still together on a
grip that passed the check above, the channel is the broken part, not the
thread. Say so, keep the hand where it is, and do not spend an insertion on a
side you never measured: a wrong side is not one wasted block, it is the
episode, and the whole run goes down it.

**Why the legs carry a feed.** The feed is not what separates them — they differ
by twice the lead whatever they are fed — so this skill used to turn them in
place. What the feed buys is a slide the thread drives for a known reason: over
the pitch it holds the glass back by a commanded amount whatever the grip is
doing, and the reading is there to be had. Turned in place, the whole reading
rests on the thread's willingness to drive the glass through a loaded grip, and
on a live grasp it has not. On 2026-10-10 a run turned its 20° legs in place:
the fields never moved more than a couple of pixels off their reset baseline,
and the pair died on the run's own 0.70 gate — one leg rejected at 0.25
coherence while the other read 0.91 and 0.83. The environment's own expert turns
its legs at 6.15 mm a turn and reads them with no gate at all: its passing runs
read the pair −13.5 against −2.9 px and −19.2 against −1.5, 0.59 and 0.98 mm
apart, on a grip whose right pad sat at 0.24 to 0.31 coherence through the whole
probe. Read the pair the same way.

What the feed does cost is the pads, so the pair is run once. Both legs and both
returns sink the hand by |angle| × feed whatever the sign — the servo winds the
descent on the angle's magnitude, not its direction — and nothing brings it
back, so the pair at 30° buries the hand about 2 mm deeper in the glass and the
widening at 45° half again. Compare what one run spent on 2026-10-10: eight legs
at 5.5 mm a turn, half the probe's over-feed, sank the hand 5.4 mm and walked
the pad-force running maximum 71 → 102 → 127.4 N into the 128 N guard. The fault
was the count — eight legs and four returns — not the feed on them
([[lattice-pitch-budget]]).

**There is no default.** A leg that comes back with no contact, or off a pad
that has lost its surface, is not a leg — it is a missing measurement, and the
answer is a real one from a fresh grip, never a coin flip between the two
directions. Nor is it a zero, and here the difference is the whole decision. The
in leg reads lead − feed a turn and the out leg −(lead + feed), so the out leg
is the lower of the two whatever the feed and whatever the pitch. A helper that
writes 0.0 in a missing leg's place is therefore not filling in a neutral value
— 0.0 sits above the out leg's reading every time, so it is casting a vote for
the loosening side, and casting it against a reading that is real. And the pair
is not ended by one bad side: both legs are turned whatever the first one read,
so the pair is decided on two real numbers or reported missing.

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
than come back. A widened pair on a fresh grip is affordable — the widening
costs half again the pair's own 2 mm — and it is what ends with an answer. On
2026-10-10 the evening run took the other
way out: twelve legs and three probe blocks all rejected, and then a written-in
+1 turned the loosening way for 1044° of wrist, backed the bulb 10.2 mm out of
the socket and crushed the glass — the direction was never once read, and the
run still spent itself on it. A pair that will not separate on a grip that
passed the check above is a verdict: the channel is out. Report it and stop.

## Traps

- The feed on a leg is not free: it sinks the hand by its own travel and nothing
  brings it back, so the pair is turned once and widened once — never retried at
  a shorter length to sharpen a reading that already separated, never run twice
  on one grip ([[lattice-pitch-budget]]).
- The loosening leg slides (lead + feed) × the angle over 360 — 0.88 mm at 30°
  on this bulb, past the size the lattice follows exactly — and comes back about
  a fifth under the arithmetic (the expert read −13.5 px where the thread's
  numbers give −15.8). Its sign and its side of the pair are what decides, never
  its size, and a leg is not a way to measure the lead.
- The residual of a round trip is mostly hysteresis rather than signal, so a
  return leg is not a second opinion.
- Direction follows the thread's handedness, and like the lead it is a per-run
  quantity to be measured on the run that uses it ([[lead-by-overshoot]]).
