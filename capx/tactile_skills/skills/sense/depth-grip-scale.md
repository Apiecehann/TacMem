---
id: S1
name: depth-grip-scale
family: sense
summary: Read grip tightness as min(left, right) depth — 34 mm no contact, 27-28 mm light, 20 mm hard stop.
task_types: [classify, insert, manipulate, screw]
channels: [depth]
objects: any
evidence: Common to every tactile task in the repo; the 34 mm sentinel is the open-pad reading on this rig, and a compliant object bottoms out near 26.4 mm. The few tenths of a millimetre between the two pads is a mounting property of the fingertips, not of the object.
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
| 27-28 mm | a light grasp, enough to hold against gravity |
| ~26.4 mm | a compliant object has bottomed out; this is as far as it goes |
| 20 mm | the hard stop |

## Traps

- The far plane is a sentinel. A pad reading 34.0 mm is not touching anything,
  and everything else it reports — shear, coherence — is meaningless.
- **0.0 mm is the other sentinel, and it is the dangerous one.** A pad that
  never reported comes back as 0.0, which sits past the hard stop and reads as
  the firmest grip on the scale. Out-of-range down means a dead sensor, not a
  crush — check the contact flag on that pad before believing the number.
- A grip that closed on nothing looks like a deep grip in the *commanded*
  position and like 34 mm in the *reading*. Depth is measured, position is
  commanded; when the object is not where it was expected they disagree.
- **Depth says how tight the grip is, never what the object weighs or how hard
  it is.** Weight is a shear difference between the two pads
  ([[gel-shear-weight]]); hardness is a *change* in depth under a commanded
  change of position. The failure is silent: depth returns a plausible
  in-range number for every object, so a criterion built on one depth alone
  looks like it is working.
