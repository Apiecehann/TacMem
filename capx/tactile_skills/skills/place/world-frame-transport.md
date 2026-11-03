---
id: P1
name: world-frame-transport
family: place
summary: Use world-frame translation deltas to preserve wrist orientation while transporting a held object; lift clear first.
task_types:
- classify
- insert
- manipulate
- wipe
channels: []
objects: any
evidence: Original P1 compares orientation-changing pose commands with translation-only transport; only the motion guidance is retained.
---

## How

Use move_delta(dx=..., dy=..., dz=...) for transport. Account for actual measured position instead of assuming a requested motion completed.

## Traps

World-frame rotations can move the arm through an arc; use tool-frame rotations for in-place orientation correction. Stay within documented motion limits.
