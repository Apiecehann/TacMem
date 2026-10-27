---
id: A7
name: stall-means-jam
family: act
summary: A part that stops sinking, or slip that keeps growing, is jammed — back off and retry.
task_types: [insert]
channels: [force, depth, shear]
objects: the insert tasks' pegs and connectors
evidence: Slip magnitude alone misclassifies — the friction of a connector passing the socket's lip shears the markers as hard as a jam does, and then decays within a step or two.
copies:
  - docs/univtac_api_reference.md §3.13
---

## How

Read the displacement increment in tiers rather than as a yes/no:

| increment | what it is | what to do |
|---|---|---|
| <= 0.5 px | noise | keep going |
| 0.5-3 px | friction or contact deformation | neither is a jam; keep descending |
| 4-8 px, decaying within 1-2 steps | passing the socket's lip | keep descending, do not pull back |
| >= 3 px sustained over many steps, part no longer sinking | a real jam | lift <= 2 steps, nudge sideways, retry |

A press that goes flat — depth change under 0.5 mm over 5 steps — is the other
jam signal, and it is the one a reflex can stop on directly
([[reflex-stop-on-events]]).

## Traps

- **Slip does not equal jam.** The lip's friction produces the largest numbers
  in the whole motion and is the case where pulling back is exactly wrong.
- A plateau means jammed *or* seated, and the plateau itself does not say
  which. Position decides: a part that reached its depth is seated
  ([[small-step-insertion]]).
- Adding force is the one response that cannot work. The part is already
  against something; more travel goes into the part or into the fixture.
