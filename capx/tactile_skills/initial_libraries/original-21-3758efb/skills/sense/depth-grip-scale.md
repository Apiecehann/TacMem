---
id: S1
name: depth-grip-scale
family: sense
summary: Grip tightness is min(left, right) depth — 34 mm no contact, 27.8-29 mm touch without grip, 24 mm a hard press — the servo takes a rigid part deeper (a 20.5 target lands at 20.16-20.75).
task_types: [classify, force, insert, manipulate, screw, wipe]
channels: [depth]
objects: any
evidence: Common to every tactile task in the repo; the 34 mm sentinel is the open-pad reading on this rig, and a compliant object bottoms out near 26.4 mm. The few tenths of a millimetre between the two pads is a mounting property of the fingertips, not of the object. A close that met nothing raises and ends on the 34.00 mm far plane on both pads (blind_pick, 2026-10-20, six sites). A returned close at 27.78-28.98 mm with under 0.01 px of marker displacement is a graze — the gel was pressed, the object was not caught — and those sites shoved the block 11-15 mm (blind_pick, 2026-10-20). The raw displacement is not exactly zero — an untouched pad reads a few thousandths of a pixel, which prints as 0.00 and drifts upward slowly with the sim's image noise. 24.0 mm is a hard press, not a deeper grip; the servo takes a rigid part deeper — a 20.5 target landed at 20.16-20.75 (grasp_classify, 2026-09-14). A returned close with one pad there, the other at 26-27 mm and the two displacements 30-130x apart is a finger on the object's top face (blind_pick seeds 11 and 13, 2026-10-20), and both came apart within the first 20 mm lift, while the holds that survived read both pads at 26.3-27.5 (seeds 4, 9, 15).
copies:
  - docs/univtac_api_reference.md §3.9
  - univtac_skill/SKILL.md (tactile reading guide)
---

## How

`left_depth_mm` and `right_depth_mm` are how far each fingertip has travelled
into the gel. Take `min(left, right)` as "how tight this grip is": the pads sit
a roughly constant 0.3 mm apart, so the deeper pad is the one carrying the
contact. **Never the mean** — averaging folds the mounting offset into the
measurement and halves the signal you were after.

Read it as a scale, not as a length:

| reading | means |
|---|---|
| 34.0 mm | nothing is pressing this pad — the sentinel, not a measurement |
| 27.8-29 mm | at the gel face: a light grasp, or a graze that pressed the gel without trapping the object; marker displacement tells them apart (over ~1 px = caught, ~0 = graze) |
| 26.2-26.8 mm | a servoed close at a 26.5 target; a compliant object has bottomed out here |
| 24 mm and below | a hard press — the servo takes a rigid part deeper (a 20.5 target landed at 20.16-20.75) |

The 27.8-29 mm band is the one to be suspicious of: a light grasp and a
graze — a close that pressed the object's edge without catching it — read
there, and both contact flags light. The depth alone cannot separate them; a
real grasp deforms the gel and shows marker displacement, a graze stays under
0.01 px ([[grasp-verification]]). A close that met *nothing* is not in this
band at all: it raises, and the pads end on 34.0 mm with the fingers shut.

## Traps

- The far plane is a sentinel. A pad reading 34.0 mm is not touching anything,
  and everything else it reports — shear, coherence — is meaningless.
- **0.0 mm is the other sentinel, and it is the dangerous one.** A pad that
  never reported comes back as 0.0, which sits below any real reading and
  reads as the firmest grip on the scale. Out-of-range down means a dead
  sensor, not a crush — check the contact flag on that pad before believing
  the number.
- **A pad pinned at 24.0 mm with the other at 26-27 mm is a finger on the
  object's top face, not a deeper grip**, and it comes apart on the first
  lift.
- A close that met nothing has one face, not two: it raises, and with the
  fingers shut the pads sit on 34.0 mm. A 27.8-29 mm reading while shut means
  something was pressed — the marker displacement says whether it was caught.
  Depth is measured, position is commanded; when the object is not where it
  was expected they disagree.
- **Depth says how tight the grip is, never what the object weighs or how hard
  it is.** Weight is a shear difference between the two pads
  ([[gel-shear-weight]]); hardness is a *change* in depth under a commanded
  change of position. The failure is silent: depth returns a plausible
  in-range number for every object, so a criterion built on one depth alone
  looks like it is working.
