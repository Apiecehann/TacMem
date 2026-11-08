---
id: A26
name: strap-load-is-shear-difference
family: sense
summary: At position=0.22, measure the left-minus-right shear change from the close; the retained calibration
  is 0.93 N/px above a 4.1 px take-up.
task_types:
- force
channels:
- shear
objects: the elastic strap, gripped by the tab at its top
evidence: Retained applicable rules from fixed source A26; unavailable-channel rules removed. No new execution
  validation is claimed.
---

## How

Retain a baseline immediately after the position-commanded close. Measure (left_marker_shear_dx - right_marker_shear_dx) relative to that baseline; the existing calibration uses 0.93 N per pixel beyond the 4.1 px take-up. The first approximately 2 mm of pull can be slack. Use motion-conditioned changes to estimate this episode's stiffness, not a previous seed's height. The original basic-feedback sensor-validity checks are unavailable, so their satisfaction cannot be assumed. Preserve direction, calibration conditions and any remaining quality evidence; do not manufacture a missing measurement.
