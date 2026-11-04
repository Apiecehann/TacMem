---
id: A12
name: feed-must-match-lead
family: act
summary: For threaded motion, command rotation and descent together with an explicit feed_lead_m; this rule supplies no lead estimator.
task_types:
- screw
channels: []
objects: light_bulb
evidence: Original A12 records the distinction between simultaneous feed and two separate motions.
---

## How

Use the native simultaneous rotation/feed option; the feed is distance per full turn in metres. Determine the feed from available task information; this rule supplies no lead estimator.

## Traps

A separate rotation followed by a descent is not the same trajectory. Stay within the native per-call limits.
