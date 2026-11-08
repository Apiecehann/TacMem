---
id: A23
name: search-refine-around-contact
family: act
summary: A returned close that did not grip is still a touch — the pads shoved the block ~11-15 mm along the sweep. Do not probe its neighbours, which sit behind it; jump ~40 mm along the sweep and close again. Sweep west to east on a lattice with about 35 mm between sites; a 14 mm grid only spends the simulated-time budget.
task_types: []
tasks: [blind_pick]
channels: [depth, displacement]
objects: the hidden block in the blind box
evidence: Measured 2026-10-19/20 in the blind_pick box. A close with nothing in reach raises and leaves both pads on the 34.00 mm far plane. A close that returns has pressed a pad below 28.5 mm and reads 27.5-28.0 whether it gripped the block (1.5-68 px of marker displacement) or only grazed a corner (under 0.001 px) — the grazes measured 27.801/27.968, 27.846/27.792 and 27.859/27.760 with ~0 px, and each shoved the block east by 14.8, 14.6 and 11.1 mm. So the marker displacement separates grip from graze, and the return/raise separates touch from empty; depth alone separates neither. From the graze site at (0.520, 0) the block's centre sat 39.6 mm ahead; a jump to (0.558, 0) closed at 27.460/27.134 with 7.2587/7.7206 px and held a 150 mm lift. The touch band is at least 29.6 mm (the furthest measured touch), the grip band about 17 mm (a block 24.8 mm from the site grazed; one 8.4 mm away gripped). In the gpt-6-astra seed-5 run a returned close at 27.8-28.4 with ~0 px was treated as a located block, and the queue walked a 6 mm neighbour chain while each close pushed the block ~15 mm further; the block at (0.5371, 0.0320) was never reached. Seed 6 read a frozen 28.05/27.97 with under 0.01 px at six consecutive sites. The 2026-10-19 rerun of seed 4 walked 7 mm east per site and never tested its own 45-site grid, and its 60 s simulated-time budget ran out first.
---

## How

A close that returns has the block within the fingers' reach; what it does not
say is whether the block was caught or merely brushed. Split that by the rise
in marker displacement across the site (cruise-height read to shut-gripper
read — the field is cumulative since reset, so never compare it to zero):

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

- **A returned close is not an empty close.** Empty closes raise and leave
  both pads on 34.00; a return means a pad went below 28.5. Measured returns
  at ~0 px sit at 27.80-28.40.
- **A graze is not a location.** The block is not at the touching site — it
  is 28-44 mm ahead of it, and it moved there during the close. Re-probing the
  same neighbourhood is probing where the block used to be; the seed-5 run
  walked a 6 mm neighbour chain and never reached it.
- **Depth cannot separate the two.** A 27-28 mm reading is a light grasp or a
  graze; only the rise in marker displacement says which
  ([[shear-vs-displacement]]).
- **The displacement is cumulative since reset.** Its absolute value answers
  "has this pad ever touched anything this episode", not "did this site touch
  the block". Take a cruise-height read before the descent and subtract;
  an empty site adds ~0.002 px, which prints as 0.00.
- **A miss is weaker evidence than a touch.** A close that raises says only
  that the block is not within the fingers' reach of that site; do not
  re-order the list around it.
- A one-sided close is not a touch for this rule — one pad on the far plane
  means the fingers pinched a face or a corner ([[search-close-default]]).
