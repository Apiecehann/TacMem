---
id: S11
name: texture-slope-two-grips
family: sense
summary: Texture is the anisotropy rise per mm of depth between force_threshold 27.5 and 20.5 — rough ~0.10, smooth ~0.03, split 0.06.
task_types: []
tasks: [grasp_classify]
channels: [anisotropy, depth]
objects: the textured blocks on this rig
evidence: The anisotropy slope separated the classes with no overlap; coherence, displacement and twist-slip were all tried on the same objects first and separated nothing.
copies:
  - docs/univtac_api_reference.md §2, §3.9, §5.1
---

## How

Anisotropy is the marker image's row gradient divided by its column gradient —
1.0 means isotropic, higher means the deformation has horizontal structure, which
is what a surface texture imposes on the gel.

Close light, read; close firm, read. Then

```
slope = (mean anisotropy change over the two pads) / (min-depth change)
```

using the same pad in both states so the mounting offset cancels.

## Traps

- Take the two readings at **commanded** grips, so the difference cancels
  wherever the first close landed.
- Only anisotropy carries this. Every other tactile scalar is an average over
  the whole marker image, and an average cannot see structure — see
  [[channel-selection]].
- One anisotropy reading on its own says nothing — it is the *change* across
  two depths that separates the surfaces ([[channel-selection]]).
