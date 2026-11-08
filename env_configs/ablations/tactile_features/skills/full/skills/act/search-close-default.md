---
id: A22
name: search-close-default
family: act
summary: Close every search site with the default adaptive close in try/except; a raise means no pad got under ~28.5 (empty sites end on the 34.00 far plane), so move on; a return means a pad pressed something (under ~28.5), a touch even at ~0 px. Skip a return with one pad on the far plane. A grip needs either pad's displacement to rise by over ~1 px; at ~0 px it is a graze that shoves the block ~15 mm, and the servo raises on it. A hold reads both pads in 26.3-27.5; one pad pinned at the 24 mm floor with the other at 26-27 and displacements tens of times apart is a finger on the block's top face, and it comes out on the first lift. Deepen to force_threshold=26.5 only when a gripped site still reads above ~27.5 on either pad. Open once per miss, in the cleanup.
task_types: []
tasks: [blind_pick]
channels: [depth, coincidence]
objects: the hidden block in the blind box
evidence: Seed 4 (2026-10-19) ran twice. The first run called close_gripper(adaptive=False) with no position at every candidate. On air it returned 34.00 mm with no error; at the block it read 24.00/24.00 mm with marker displacement 71.53/57.09 px, and a second site read 87.19/83.01 px with "Slip detected!" — the block was lost on the lift both times. The later run (gpt-6-astra trial_04) used the default close. Its sites read 27.80/27.97 and 27.82/27.87 with under 0.01 px of marker displacement; a 2026-10-20 rerun of those same sites showed what those readings are. The closes returned (a pad had pressed below 28.5 mm) and each shoved the block 11-15 mm east — grazes, not empty closes. The empty close is the one that raises; every raised close in that run ended at 34.00/34.00, the undeformed far plane. A servoed close at the block's true position read 26.57/26.23 with 15-68 px; a replay of the seed-6 site gave 26.39/26.47 on the first explicit close, while that run's own 26.35-26.65 window rejected it. The released reading around a 26.5 target was measured at 26.23, 26.32 and 26.68. The two passing seeds (1 and 2) used the default close and ended at 26.3-27.8 mm with 1.5-5.5 px of displacement. The v13 expert recipe targets 26.5 mm because a first touch at 27.55/27.39 was measured at 28.26/27.95 during the lift, with the block still between the pads (univtac_blind_pick.py). The 2026-10-20 live probe (seed 4, block reset at (0.5448, 0.0013)) separated the three outcomes. All six empty sites raised with 34.00/34.00 on both pads; the returned closes read 27.801/27.968 at (0.520, 0), 27.846/27.792 at (0.534, 0) and 27.859/27.760 at (0.545, 0), each with under 0.001 px of displacement, and each moved the block east by 14.8, 14.6 and 11.1 mm; a servoed 26.5 close at the first of those sites raised. A jump to (0.558, 0), 8.4 mm from the block's new centre, returned 27.460/27.134 with 7.2587/7.7206 px and held through a 150 mm lift (block z 0.0164 -> 0.1648). The 2026-10-19 23:28 rerun of seed 4 closed four times on such grazes; the one site that gripped read 75.81/77.07 px, and the 60 s simulated-time budget ran out before the search reached the block again. Seeds 11-15 (2026-10-20) separated the jam from a hold. The holds that survived read 26.3-27.5 on both pads (seed 15 read 27.48/27.14 with 6.70/6.54 px, held through a 0.293 m lift), while a returned close with one pad pinned at 24.0 mm and the other at 26-27 mm, displacements 30-130x apart (seeds 11 and 13 read 3.86/92.63 px and 66.15/141.59 px), lost the block within the first 20 mm lift. 24.0 mm is the gsmini camera's near limit (clipping window 24-34 mm), not a deeper grip.
---

## How

```python
try:
    reading = close_gripper()   # adaptive, task-default firmness
except RuntimeError:            # no pad got under ~28.5: the site is empty
    open_gripper()
    continue                    # this candidate is a miss
```

The default close keeps adapting until the fingers meet resistance, so it can
land on a block of unknown width without being told where to stop. It is also
the only probe that catches a block centred between the pads: the descent
leaves it untouched when its faces sit between the fingers, but the close
pinches it. So make the close, then read *whether the call returned* before you
read any depth:

- **Raised** (`Gripper action failed`): no pad got under 28.5 mm. Measured
  empty sites end on 34.00 / 34.00 — the undeformed far plane. Nothing is
  between the pads, so this site is a miss.
- **Returned** (`OK`): at least one pad is under 28.5 mm — the gel was
  pressed, so the fingers met the block. The returns measured 27.46-27.97 on
  both pads. A return is a touch even when the marker displacement is ~0.

A touch is not a grip. Split the returns by the rise in marker displacement
(cruise-height read to shut-gripper read; the field is cumulative since reset,
so compare two reads, never the absolute against zero):

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

```python
surface = (reading["left_depth_mm"], reading["right_depth_mm"])
if max(surface) >= 31.0:        # one pad never met the block
    open_gripper()              # one-sided pinch: skip the site
    continue
try:
    reading = close_gripper(force_threshold=26.5)   # servoed, both pads
except RuntimeError:            # the servo could not reach the target
    open_gripper()
    continue
```

A grip that already reads 27.1-27.5 on both pads needs no second close: a
probe grip at 27.460 / 27.134 with 7.26 / 7.72 px held through a 150 mm lift.
Every close costs about 6.5 s of simulated time out of the task's 60 s, so
spend the second close only where the margin is missing.

`force_threshold` is a gel-depth target and the close is servoed, so it lands
close to where it is told — not exactly: 26.23, 26.32 and 26.68 were measured
around a 26.5 target. 26.5 mm is the deepest target the servo can reach on a
compliant object; below it the close fails outright rather than stopping
short. Accept both pads anywhere in 26.2-26.8; a window of exactly
26.5 ± 0.15 rejected three real closes in the seed-6 run. Do not open between
the two closes: the second starts from where the first stopped and steps
inward. Read both depths back before lifting ([[grasp-verification]]).

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

- **`close_gripper(adaptive=False)` with `position=None` is not a search
  close: it drives the fingers fully shut.** It does not stop at the block.
  Seed 4's closes at the block read 24.00/24.00 mm with 71-87 px of marker
  displacement, against 1.5-5.5 px at the end of the passing runs, and both
  lost the block.
- Do not verify that close on depth alone: 24.00 mm is below the gel face
  (28.3), so a `depth < 28` check accepts it. The marker displacement is what
  says the pads were dragged across the object ([[shear-vs-displacement]]).
- **A close that raises is information, not a broken gripper.** No pad reached
  28.5 mm; the measured empty site ends on the 34.00 far plane. Catch it and
  treat the column as a miss ([[search-continues-after-miss]]).
- **A returned close at ~0 px is a graze, not a miss.** The gel was pressed
  below 28.5 mm, so something was there — measured 27.80-27.97 with under
  0.001 px, and the block moved 11-15 mm each time. Discarding these as
  "empty" was the 2026-10-20 failure: two real touches were thrown away and
  the search walked past a block it had just pushed.
- **A 27-28 mm grip is the edge of the window, not a hold.** The hold needs
  both pads below 28.0 mm for 18 consecutive steps, so a first touch sits
  0.3-1.3 mm inside it and has no margin for the drift a lift causes. Deepen
  the close before lifting when either pad is still above 27.5.
- **Do not deepen a one-sided close.** If one pad is still on the far plane
  (34.00), the fingers are pinching one face; closing harder drags the block
  sideways and spoils the columns beside it. Open and take the next candidate
  ([[search-continues-after-miss]]).
- **A graze does not improve on a second close.** There is nothing between the
  pads, so every further servoed close walks its rung ladder, raises, and
  costs roughly 200 control steps — about a third of the search's 60 s budget
  when it happens three times.
- **Do not reopen a gripper the cleanup already opened.** An open is ~80
  control steps; doing it at both ends of every candidate doubles that for no
  state change. Track whether the previous cleanup open raised before opening
  again.
- **A pad pinned at 24.0 mm is bottomed out, not gripping deeper.** The camera
  window is 24-34 mm, so the reading cannot go lower whatever the object does.
  One pad on 24.0 with the other at 26-27 mm and the two displacements 30-130x
  apart is a finger standing on the block's top face: seeds 11 and 13 lost the
  block within the first 20 mm lift, while the holds that survived read both
  pads at 26.3-27.5 (seeds 4, 9, 15).
