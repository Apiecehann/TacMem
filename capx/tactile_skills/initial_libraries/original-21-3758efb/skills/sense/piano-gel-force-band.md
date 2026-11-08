---
id: S25
name: piano-gel-force-band
family: sense
summary: Press to 0.35 N light and 0.50 N heavy; above 0.43 N the estimate saturates and the pad's tilt plateau is the signal; lift a few mm before opening the finger, or the force spikes past the band.
task_types: []
tasks: [arm_shadow_piano]
channels: [force, depth]
objects: the white keys of this piano rig, under the index GelSight Mini
evidence: Model fitted on 300 labelled press frames from the seven white keys (one depth image per 0.1 mm palm step, PhysX key force as the label). Leave-one-key-out error 0.0115 N MAE and 0.0349 N P95. Closed loop with a fresh episode per press, stopping on the live estimate and reading the true force only at the stop -- light 7/7 in band (0.352-0.374 N), heavy 7/7 (0.438-0.459 N). The pad's depth gradient plateaus at 0.443-0.456 N on all seven keys. Served end to end through the cap-x server, one oracle trial of six notes (seed 1) held 0.440/0.443/0.442 N on its three heavy notes and 0.327/0.344/0.342 N on its three light ones, every note in band, reward 1.0. Measured 2026-11-06 on the GelSight Mini / UIPC rig.
---

## How

The index gel's image carries the key force, and the task serves it as an
estimate: a model fitted on measured presses reads five whole-image statistics
of the depth change since the no-contact frame, plus the pad's depth gradient,
and returns newtons. `left_force_N` is that estimate, and
`close_gripper(press_force_N=...)` servos the press on it -- it returns the
value it stopped at.

Press to 0.35 N for a light note and 0.50 N for a heavy one. The force bands
are 0.28-0.42 N and 0.43-0.57 N; a force above the band for more than a moment
ends the run, so do not ask for more than the target.

The channel that carries the force is the **tilt of the whole pad**, not the
indentation: the key rotates with the finger, the pad's indentation stays under
0.15 mm, and the contact moment tilts the gelpad by up to 0.4 mm across its
face. A press that reads only the depth minimum sees almost nothing.

## Traps

- **Above about 0.43 N the estimate stops separating forces.** The pad's tilt
  bottoms out, so 0.45 N and 0.55 N read the same. The servo knows this: a
  target above the saturation point is served by descending until the tilt
  stops growing, which is the heavy band. Do not expect `left_force_N` to read
  back a 0.50 N request -- it comes back near 0.47.
- **`left_force_N` is None, not 0.0, before a press has captured its
  no-contact frame.** None means "no reference"; 0.0 would mean an unloaded
  pad. The press captures the frame itself, so read the estimate after
  `close_gripper(press_force_N=...)`.
- **`left_force_quality: out_of_range` is not a force reading.** It means the
  image left the model's calibrated envelope (a different press pose, a lost
  contact, a stale reference) and the number is extrapolating. Re-press from
  the key's `press` pose rather than trusting it.
- The recipe is fixed: approach 2 mm above the press pose, close with the
  ramp, descend in 0.1 mm steps. The reference frame is captured inside that
  recipe, so a press assembled by hand from moves and closes has no reference
  and produces no estimate.
- **Unload before opening the finger.** Opening it while the palm still holds
  the key drags the pad across the key and the contact force spikes for a
  control step -- on a heavy note that spike reached 0.58 N against a 0.57 N
  over-force line. Lift a few millimetres first, then open.
- **The episode ends the moment the last note is released.** A motion issued
  right after can come back with "the episode is over"; that is the finish,
  not a failed note.
