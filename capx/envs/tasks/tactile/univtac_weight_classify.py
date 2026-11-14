from capx.envs.tasks.base import CodeExecutionEnvBase

PROMPT = """
You are controlling a Franka Emika robot with GelSight Mini tactile sensors
on both fingertips. The API is described below.

Goal: exactly ONE block is standing on the table. Decide by touch whether it is
light or heavy, then place it on the matching plate:
  - Light block -> the blue_plate
  - Heavy block -> the yellow_plate

In the scene the block is named "block" and the plates "blue_plate" and
"yellow_plate" - use those exact names with the pose and grasp helpers.

The catch: the other block is parked a metre away, out of reach, so there is no
reference object to compare against. The reading has to be absolute, taken the
same way every trial.

Your fingertips are the instrument: the weight is in the gel, not in how far
anything moved, and a single raw reading means nothing on its own. Which grip
to use, how to take the reading, and how to turn it into a weight are in the
tactile skills at the end of this prompt.

After classifying, carry the block to its plate. The placement recipe - how to
move a held block, where to stop, and whether to let go - is in those same
skills. This task is scored on where the block ends up: seat it on the right
plate, upright, and stop.

Final placement: within 4 cm horizontally and 1 cm vertically, upright within 15
degrees.

Write ONLY executable Python code (no code fences). Import numpy if needed.
"""

# The trace arm delivers the frozen original-21 library's routed set (seven
# skills): the weight split (``grip-force-by-mass``, 0.2 px), the weigh-by-lift
# protocol (``weigh-grasp-by-lift``), the shear channel (``gel-shear-weight``,
# ``shear-vs-displacement``) and the shared classify rules.  The old library
# has no placement family, so the prompt's placement promise is rewritten; the
# unattended paragraph is this harness's own requirement, and the printing
# request exists because this task's check detail carries no measurement -- the
# previous attempt's stdout is the only place a later seed can see what the gel
# read.
PROMPT_TRACE = PROMPT.replace(
    "The placement recipe - how to\n"
    "move a held block, where to stop, and whether to let go - is in those same\n"
    "skills.",
    "How to move a held block, where to stop, and whether to let go\n"
    "have to be worked out from your attempts.",
).replace(
    "Write ONLY executable Python code",
    "The program runs unattended: nothing can be typed in, and input() is not\n"
    "available. Print the grip you command, the gel readings you act on, and what\n"
    "you concluded from them, so a later attempt can see them.\n\n"
    "Write ONLY executable Python code",
)
assert PROMPT_TRACE != PROMPT, "PROMPT_TRACE lost its anchor; update the marker"

ORACLE_CODE = """
import time

# A rigid block pins the fingers, so the depth servo lands on the same reading
# whatever the block weighs -- the weight has to come off the gel instead.
# ~28.5 is light enough that the hanging load shears the pads, and still firm
# enough to carry the block; by ~26 the shear collapses and the weight stops
# showing.
GRIP_LIGHT = 28.5
GRIP_FIRM = 28.0            # a touch firmer if the light grip drops it
LIFT_STEPS = 3
LIFT_STEP_M = 0.01
SETTLE_S = 0.4
HANG_SAMPLES = 4
SHEAR_HEAVY_PX = 0.2        # heavy 0.72-0.99 px, light 0.001-0.045 px (n=12)


def median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def measure_weight(force_threshold):
    \"\"\"Grasp the block lightly, lift it in steps, and read the weight off the gel.\"\"\"
    grasp_pos, grasp_quat = sample_grasp_pose("block")
    goto_pose(grasp_pos, grasp_quat, z_approach=0.05)
    close_gripper(force_threshold=force_threshold)

    # Baseline: the block is still on the table, so nothing hangs from the gel
    # yet and any shear here is just the squeeze the close produced.
    baseline = get_tactile_data()

    for _ in range(LIFT_STEPS):
        move_delta(dz=LIFT_STEP_M)
        time.sleep(SETTLE_S)

    # Sample a few times: under load the block keeps creeping in the grip.
    dl, dr = [], []
    for _ in range(HANG_SAMPLES):
        fb = get_tactile_data()
        dl.append(fb["left_marker_shear_dx"] - baseline["left_marker_shear_dx"])
        dr.append(fb["right_marker_shear_dx"] - baseline["right_marker_shear_dx"])
        time.sleep(SETTLE_S)

    # The pads are mounted mirrored, so one slide shows up with opposite sign
    # on the two of them.  Subtracting cancels the squeeze (common-mode, and
    # much larger than the load's share) and adds the load's contribution.
    left, right = median(dl), median(dr)
    return abs(left - right), left, right, grasp_pos, grasp_quat


weight, dl, dr, grasp_pos, grasp_quat = measure_weight(GRIP_LIGHT)

# A block that never left the table would read as light, so make sure it is
# actually hanging before trusting the reading.  This is a check on whether
# the grasp took -- it is not part of the weight judgement, which is tactile.
block_pos, _, _ = get_object_pose("block")
if block_pos[2] < 0.02:
    print("block still on the table - retrying with a firmer grip")
    open_gripper()
    weight, dl, dr, grasp_pos, grasp_quat = measure_weight(GRIP_FIRM)

label = "heavy" if weight > SHEAR_HEAVY_PX else "light"
print(f"gel shear differential={weight:.3f}px (L {dl:+.3f}, R {dr:+.3f}) -> {label}")


def move_by(dx=0.0, dy=0.0, dz=0.0):
    \"\"\"move_delta caps each axis at 0.10 m per call, so chunk long moves.

    It also has a 2 mm FLOOR: a smaller delta is rejected, not executed.  So
    stop once the remainder is under that, and leave the last millimetre or
    two unmoved -- the placement tolerance is 2 cm.
    \"\"\"
    for key, val in (("dx", dx), ("dy", dy), ("dz", dz)):
        while abs(val) > 0.002:
            step = max(-0.05, min(0.05, val))
            move_delta(**{key: step})
            val -= step


# --- Place on the matching plate ---
plate = "yellow_plate" if label == "heavy" else "blue_plate"
plate_pos, _, plate_ext = get_object_pose(plate, return_bbox_extent=True)
rest_z = plate_pos[2] + plate_ext[2]          # plate top; origins sit at the bottom face

# Carry the block across with world-frame deltas: it is held rigidly, so moving
# the hand by d moves the block by d, with no need to know the hand's pose.
# Deliberately NOT goto_pose: re-commanding the wrist to an absolute
# orientation while the block touches down twists it inside the light grip --
# measured 11-45 deg of tilt that way against 0.2-0.3 deg this way.
# Horizontal first (the block is well clear of the 6 mm plate), then down.
block_now, _, _ = get_object_pose("block")
move_by(dx=plate_pos[0] - block_now[0],
        dy=plate_pos[1] - block_now[1],
        dz=rest_z + 0.002 - block_now[2])

# Seat it: step the descent down until the block rests on the plate.
for _ in range(12):
    p, _, _ = get_object_pose("block")
    gap = p[2] - rest_z
    if gap <= 0.002:
        break
    move_delta(dz=-max(gap, 0.002))

# Stop here, still holding the block.  Do NOT open the gripper.  The block is
# only 1.8 g, so the gel holds on to it: when the jaws come apart they drag it
# off the plate or tip it over (8 failures in 11 tries on one seed, and neither
# pressing it down first nor re-gripping more gently helped).  The task is
# scored on where the block ends up, not on whether the hand let go, and
# ViTaForge's own expert places it the same way -- place_actor(is_open=False)
# with no open afterwards.
check_success()
"""


class UniVTACWeightClassifyCodeEnv(CodeExecutionEnvBase):
    prompt = PROMPT
    oracle_code = ORACLE_CODE


class UniVTACEmptyCanSelectCodeEnv(CodeExecutionEnvBase):
    tactile_skills_verbose = False
    tactile_skills = (
        "one-call-approach",
        "gel-shear-weight",
        "shear-vs-displacement",
        "differential-across-states",
        "no-absolute-without-reference",
        "no-calibration-transfer",
        "depth-grip-scale",
        "close-lands-on-target",
        "contact-flag-is-range",
        "slip-pair-baseline",
        "lift-before-lateral",
        "world-frame-transport",
        "descend-until-supported",
        "unload-before-open",
        "settle-after-release",
        "tactile-cannot-locate",
    )

    def _tactile_skill_block(self) -> str:
        block = super()._tactile_skill_block()
        if not block:
            return block
        from capx.tactile_skills import default_library

        library = default_library()
        scopes = "\n".join(
            f"- {name}: {library.get(name).objects or 'unspecified'}"
            for name in self.tactile_skills
        )
        return f"{block}\n\nSKILL APPLICABILITY (source objects):\n{scopes}"


class UniVTACWeightClassifyTraceCodeEnv(UniVTACWeightClassifyCodeEnv):
    """The cross-seed trace experiment on weight_classify.

    The task's difficulty is the measuring procedure, not one number: the
    block has to be taken at a grip light enough that the hanging load shears
    the pads, lifted until it actually hangs, and read as the two pads' shear
    difference -- a single raw reading means nothing.

    The delivered set is the frozen original-21 library's route for this task
    (seven skills): the weight split (``grip-force-by-mass``, 0.2 px), the
    weigh-by-lift protocol (``weigh-grasp-by-lift``), the shear channel
    (``gel-shear-weight``, ``shear-vs-displacement``), the depth scale, the
    load response (``follow-is-signal``) and the servo caveat.

    What is left to learn is the procedure around the measurement: where to
    grasp, how to carry, and how to set the block down without dragging it off
    the plate.  The old library has no ``place`` family and no approach rules.
    """

    prompt = PROMPT_TRACE
    tactile_skills = (
        "depth-grip-scale",
        "follow-is-signal",
        "gel-shear-weight",
        "grip-force-by-mass",
        "position-mode-for-yield",
        "shear-vs-displacement",
        "weigh-grasp-by-lift",
    )
