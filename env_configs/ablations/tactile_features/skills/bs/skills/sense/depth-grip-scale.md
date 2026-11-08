---
id: S1
name: depth-grip-scale
family: sense
summary: Grip tightness is min(left, right) depth — 34 mm no contact, 27.8-29 mm touch without grip, 24 mm
  sensor floor.
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
channels:
- depth
objects: any
evidence: Filtered from the fixed original skill S1; removed-channel evidence is withheld. No new execution
  evidence is claimed.
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
| 24 mm | the sensor's floor — the pad is bottomed out and cannot read deeper |

The 27.8-29 mm band is the one to be suspicious of: a light grasp and a
graze — a close that pressed the object's edge without catching it — read
there, and both contact flags light. The depth alone cannot separate them; a
real grasp deforms the gel and shows marker displacement, a graze stays under
0.01 px ([[grasp-verification]]). A close that met *nothing* is not in this
band at all: it raises, and the pads end on 34.0 mm with the fingers shut.

## Traps
