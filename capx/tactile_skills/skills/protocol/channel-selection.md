---
id: R10
name: channel-selection
family: protocol
summary: Only shear and anisotropy carry structure — texture lives in anisotropy alone; every other scalar averages the image.
task_types: [classify, force, insert, manipulate, screw, wipe]
# blind_pick_balls separates its materials on the gel's fine relief, which is
# not an average of the image and is the one channel its API reports for it.
not_tasks: [blind_pick_balls]
channels: [shear, anisotropy]
objects: any
evidence: Every criterion built on an averaged channel has been falsified on this rig — displacement as weight, the single-pad shear magnitude as weight, coherence as texture, twist-slip as friction. The two that survive read a difference or an axis. Two surfaces that deform the gel by the same amount read alike on every other scalar.
copies:
  - docs/univtac_api_reference.md §3.9 and §3.4
---

## How

Pick the channel from the question:

| question | channel |
|---|---|
| which way did it slide, and how far | the **difference** of the two pads' shear |
| how does the surface shape the gel | **anisotropy** across two grip depths |
| is this frame usable at all | coherence, as a gate only |
| how tight is the grip | depth |
| did anything move | displacement, as a difference between calls |

Anisotropy is the only channel that sees a surface texture: pressed against
ridges the gel picks up banding and the number moves, against a plain surface
it stays near isotropic. A single reading only means something at a
**commanded** grip, so the usable form is the change across two grips
([[texture-slope-two-grips]]).

## Traps

- Coherence, displacement and the single-pad magnitudes all have the same
  shape — one number summarising the whole image — and that is what makes them
  unable to separate objects that differ in structure.
- An unloaded or uniformly loaded gel is isotropic too. One anisotropy reading
  on its own cannot separate "smooth" from "not pressed", which is why the
  difference across two depths is the measurement and the value is not.
- A default close lands wherever it lands, and how far anisotropy moves depends
  on how hard the gel was pressed — so an uncommanded reading is a reading of
  the close, not of the surface.
- A channel that fails once is usually failing for this reason rather than for
  want of a better threshold. Check whether the quantity keeps a sign, a
  direction or an axis before tuning it.
