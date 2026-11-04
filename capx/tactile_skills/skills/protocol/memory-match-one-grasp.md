---
id: R11
name: memory-match-one-grasp
family: protocol
summary: Probe the reference and both candidates with the same bounded sequence, retaining
  separate raw observations for each object.
task_types: []
tasks:
- tactile_memory_match
channels: []
objects: Only the tasks named by this skill; sampling conditions must remain comparable.
evidence: Sampling and observation cautions adapted from original R11; no new experimental
  outcome is asserted.
---

## How

Use the allowed object poses for approach. Save raw snapshots at matching probe stages, with object identity, side and time. Compare the candidates with the recorded reference before placing the selected object.

## Traps

Do not overwrite the reference with a later candidate or compare observations collected using different motion sequences.
