---
id: A22
name: search-close-default
family: act
summary: Close every search site with the default adaptive close in try/except; a raise means no pad got under
  ~28.5 (empty sites end on the 34.00 far plane), so move on; a return means a pad pressed something (under
  ~28.5), a touch even at ~0 px. Skip a return with one pad on the far plane. A grip needs either pad's displacement
  to rise by over ~1 px; at ~0 px it is a graze that shoves the block ~15 mm, and the servo raises on it. A
  hold reads both pads in 26.3-27.5; one pad pinned at the 24 mm floor with the other at 26-27 and displacements
  tens of times apart is a finger on the block's top face, and it comes out on the first lift. Deepen to force_threshold=26.5
  only when a gripped site still reads above ~27.5 on either pad. Open once per miss, in the cleanup.
task_types: []
tasks:
- blind_pick
channels:
- depth
- coincidence
objects: the hidden block in the blind box
evidence: Filtered from the fixed original skill A22; removed-channel evidence is withheld. No new execution
  evidence is claimed.
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
