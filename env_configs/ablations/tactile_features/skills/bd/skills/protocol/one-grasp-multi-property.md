---
id: R2
name: one-grasp-multi-property
family: protocol
summary: Read several properties from one grasp, and weigh before you squeeze — the firm clamp hardness needs destroys the weight reading.
task_types:
- classify
channels: []
objects: the classify tasks, and the memory-match cans in particular
evidence: One progressive squeeze on a can supplies hardness and the load response; a single close at the weight grip shows nothing of hardness because both hardnesses land on the same depth. An object first squeezed at a firm clamp loses its weight reading, while re-closing at the light weight grip does not spoil one.
copies:
- docs/univtac_api_reference.md §2
---

## How

Order the measurements inside the grasp by what each one destroys:

1. **weight** — baseline and hang at the light grip, and the shear differential;
2. **hardness** — the two commanded positions;

The destructive step goes last. Weight is the fragile one: it needs a light
grip that still lets the gel shear, and the firm squeeze removes exactly that.

## Traps

- Re-closing at the light weight grip after a hardness read does **not** recover
  the weight. Only an object first squeezed at a firm depth loses it, so the
  damage is done by the order and cannot be undone by repeating a step. Open,
  re-approach and start again is the only recovery.
- Re-closing at the same light grip does not spoil a weight reading, so the
  order only has to protect against the *firm* clamp, not against any contact.
- The multi-property grasp is not the standalone weight recipe with something
  prepended. The memory-match sequence is one integrated grasp, and adding
  closes to it changes the thing being measured
  ([[follow-is-signal]]).
- Before adding a third close, check whether an existing pair already carries
  the channel you want.
