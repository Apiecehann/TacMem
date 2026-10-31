---
id: S13
name: shear-vs-displacement
family: sense
summary: Pick the channel by the question — shear for which way the part slid, displacement for whether anything moved.
task_types: [classify, force, insert, manipulate, screw, wipe]
channels: [shear, displacement, coherence]
objects: any
evidence: Both scalars come off the same marker field; only shear keeps a sign per axis, which is why every directional reading in the repo (weight, thread direction, jamming) is built on it.
copies:
  - docs/univtac_api_reference.md §3.9
---

## How

The gel's printed marker lattice gives two families of scalars:

- **shear** (`left_marker_shear_dx`, `_dy`) — signed per axis. A part sliding
  across the fingers moves the markers with it, so the sign says which way, and
  the two pads see the same slide with opposite signs (they are mounted
  mirrored).
- **displacement** (`marker_displacement_px`) — a magnitude since reset. It
  answers "did the markers move", not "which way".
- **coherence** (`marker_coherence`) — how well the lattice matched at all.
  High only during genuine slip; it is a validity flag, not a measurement.

Choose by the question: *which way / how far* wants shear; *did anything move
at all* wants displacement; *can I trust this frame* wants coherence.

## Traps

- Displacement is absolute since reset and stays high after a real event, so it
  must never be compared against a threshold directly. Take the difference
  between two calls, or use `detect_slip`, which does that internally.
- Coherence has no discriminative power between objects — two surfaces that
  differ in roughness or mass can share it. Use it to reject a frame, not to
  classify one.
