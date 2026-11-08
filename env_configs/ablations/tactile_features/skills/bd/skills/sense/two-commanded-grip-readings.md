---
id: S11
name: two-commanded-grip-readings
family: sense
summary: Take two readings at commanded grips, using the same pad in both states so the mounting offset cancels.
task_types: []
tasks:
- grasp_classify
channels:
- depth
objects: the textured blocks on this rig
evidence: Only the original commanded-grip and same-pad measurement rules are retained. The S-dependent formula and evidence remain in the source snapshot.
copies:
- docs/univtac_api_reference.md §2, §3.9, §5.1
---

## How

Close light, read; close firm, read, using the same pad in both states so the
mounting offset cancels.

## Traps

- Take the two readings at **commanded** grips, so the difference cancels
  wherever the first close landed.
