---
id: A23
name: search-refine-around-contact
family: act
summary: A returned close that did not grip is still a touch — the pads shoved the block ~11-15 mm along the
  sweep. Do not probe its neighbours, which sit behind it; jump ~40 mm along the sweep and close again. Sweep
  west to east on a lattice with about 35 mm between sites; a 14 mm grid only spends the simulated-time budget.
task_types: []
tasks:
- blind_pick
channels: []
objects: the hidden block in the blind box
evidence: Filtered from the fixed original skill A23; removed-channel evidence is withheld. No new execution
  evidence is claimed.
---

## How

- rise over ~1 px: a grip. Deepen if needed and lift.
- ~0 px: a graze. The gel was pressed, so the site touched the block, but the
  block slipped out of the fingers and closing pushed it away.

A graze is a direction measurement, not a location. The close shoves the
block along the sweep: measured, the block's centre went from 24.8 mm ahead of
the site to 39.6 mm ahead, then a second touch moved it another 14.6 mm. After
a graze the block's centre is roughly 28-44 mm ahead of that site. So do not
test the neighbours — they sit behind the block now, and every close there
costs 6.5 s of the search's 60 s. Open, jump about 40 mm along the sweep, and
close again. Measured: a jump of 38 mm from the graze site landed 8.4 mm from
the block, and the close took it.

That the jump is along the sweep is what makes the sweep direction matter.
Sweep west to east, in rows about 35 mm apart:

```python
xs = [0.500, 0.535]                  # 35 mm apart, covers 0.492-0.548
ys = [-0.036, 0.0, 0.036]            # 36 mm apart, covers -0.052-0.052
sites = [(x, y) for y in ys for x in xs]   # west to east within each row
```

A touch band of ~30 mm and a 35 mm spacing still meet a block anywhere in a
cell: the worst case is the cell corner, 25.1 mm from the nearest site, 4.5 mm
inside the furthest measured touch. A 14-20 mm grid touches nothing extra and
turns the search into 20-45 closes, which is three to six times the budget.
Six sites with one close each is about 40 s of the 60 s; a jump and a second
close still fit.

West to east also keeps the geometry one-sided: with the columns 35 mm apart a
block east of the first column can only be grazed from that column (a block
further east is directly gripped), so the shove and the jump point the same
way. Approaching the same block from the east column would push it west, back
along the row already searched.

## Traps
