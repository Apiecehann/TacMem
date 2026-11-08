---
id: R10
name: channel-selection
family: protocol
summary: 'Pick the channel from the question: the two pads'' shear difference for slide, coherence as a gate, depth for grip tightness, and displacement differences for motion.'
task_types:
- classify
- force
- insert
- manipulate
- screw
- wipe
not_tasks:
- blind_pick_balls
channels:
- shear
- coherence
- depth
- displacement
objects: any
evidence: B/D rules retained from the source skill. Source evidence and removed S-dependent rules are preserved in the source snapshot and change manifest.
copies:
- docs/univtac_api_reference.md §3.9 and §3.4
---

## How

Pick the channel from the question:

| question | channel |
|---|---|
| which way did it slide, and how far | the **difference** of the two pads' shear |
| is this frame usable at all | coherence, as a gate only |
| how tight is the grip | depth |
| did anything move | displacement, as a difference between calls |

## Traps

- Coherence, displacement and the single-pad magnitudes all have the same
  shape — one number summarising the whole image — and that is what makes them
  unable to separate objects that differ in structure.
