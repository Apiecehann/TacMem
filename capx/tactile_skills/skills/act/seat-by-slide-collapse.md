---
id: A13
name: seat-by-slide-collapse
family: act
summary: A bottomed part cannot keep pace — the seat is the slide reaching a tenth of that block's own commanded descent.
task_types: [screw]
channels: [shear, coincidence]
objects: light_bulb
evidence: The seat read a slide of 0.04 mm against false slides of 0.11, 0.08 and 0.08 mm from unlocked lattices; on another seed the seat's slide was −0.043 mm, the largest of its run, at coherence 0.71/0.70 — the only block in 41 that cleared the project's own gate. On 2026-10-08 a run never made this call at all — it waited on the API's `reached` instead, which at the time required the torque already at target, so the release that ended every block threw the signal out with the torque and the same climb was replayed fourteen times until a wind-back was refused. That is no longer what `reached` means — since 2026-10-09 it reports the bottomed part, the same event this call reads off the gel.
---

## How

While the thread is taking the part in, the hand and the glass descend together
and the slide stays small. Once the part is seated the hand goes on down and
the glass does not, so the slide collapses toward the *whole commanded descent*
of that twist, running the other way.

Read it as a **fraction of the twist's own commanded descent** — a tenth of it
is the seat. Never as a fixed depth: the seat's slide scales with how far the
block was asked to go, so a threshold in millimetres moves with the segment
length.

## Traps

- **Magnitude alone cannot make this call.** A tracker that has lost its
  lattice reports an unrelated number at the same plausible size, so a slide
  that looks like a seat may be a lost marker. Hold the seat's reading to
  coherence ≥ 0.70.
- **Arm the call late.** A part still shallow in its socket has not reached a
  seat at all. Arm it only after the hand has descended most of the free travel
  (about 8 mm on the task this was measured on); a fixed depth threshold called
  a seat 3 mm in and cost that run.
- **Confidence is for this call alone.** The lead reading is deliberately
  ungated ([[confidence-gate]]).
- The nominal travel is task geometry, not a constant of the method. Check it
  against the object rather than trusting the number above.
