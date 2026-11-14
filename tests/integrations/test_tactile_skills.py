"""Tests for the tactile skill library and its routing.

No GPU, no simulator, no server: the library is files on disk, and the prompt
hook is a plain method that only reads its own attributes.  Six things are
covered:

  1. The taxonomy matches the tasks that actually exist — a renamed or added
     task fails here instead of silently routing to nothing.
  2. Routing, by type and by task.
  3. Hygiene — every skill reaches at least one task (nothing in the library is
     dead weight) and every `[[cross-reference]]` resolves.
  4. The delivered block stays cheap: one line per skill, not an essay each.
  5. The prompt hook reaches every UniVTAC task that can read the gel, and
     nothing else — the ablation configs keep the task name but not the
     sensors, and must not be handed gel recipes.
  6. A malformed skill file fails loudly at load time.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from capx.envs.tasks.base import CodeExecutionEnvBase
from capx.tactile_skills import (
    FAMILIES,
    SkillError,
    TactileSkillLibrary,
    default_library,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "env_configs" / "univtac"
LIBRARY_ROOT = REPO_ROOT / "capx" / "tactile_skills"


def test_empty_can_entry_uses_native_gelsight_config(monkeypatch):
    from capx.serving import launch_univtac_server as server

    assert "can_empty_select" in server.AVAILABLE_TASKS
    backing = server._backing_for("can_empty_select")
    assert backing.root == server.UNIVTAC_ROOT
    assert backing.config_file == "gelsight.yml"
    assert backing.vitforge_tacex
    assert backing.cfg_overrides == ()


def test_empty_can_public_names_resolve_active_actors(monkeypatch):
    from capx.serving import launch_univtac_server as server

    fanta, pepsi, basket = object(), object(), object()
    task = SimpleNamespace(active_cans={"fanta": fanta, "pepsi": pepsi}, basket=basket)
    monkeypatch.setattr(server, "_TASK", task)
    monkeypatch.setattr(server, "_TASK_NAME", "can_empty_select")
    assert server._find_actor("fanta") is fanta
    assert server._find_actor("pepsi") is pepsi
    assert server._find_actor("basket") is basket
    for private_name in ("empty_can", "fanta_light_can", "pepsi_heavy_can", "can"):
        assert server._find_actor(private_name) is None
    replacement = object()
    task.active_cans["fanta"] = replacement
    assert server._find_actor("fanta") is replacement


def test_empty_can_bbox_uses_existing_client_contract(monkeypatch):
    import numpy as np
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi
    from capx.serving import launch_univtac_server as server

    pose = SimpleNamespace(p=np.array([0.5, -0.2, 0.002]), q=np.array([1., 0., 0., 0.]))
    actor = SimpleNamespace(get_pose=lambda: pose)
    monkeypatch.setattr(server, "_TASK", SimpleNamespace(basket=actor))
    monkeypatch.setattr(server, "_TASK_NAME", "can_empty_select")
    monkeypatch.setattr(server, "_try_get_bbox_extent", lambda value: [0.162, 0.162, 0.145])
    api = object.__new__(UniVTACTactileApi)
    calls = []

    def get(endpoint, params):
        calls.append((endpoint, params))
        return server._sim_get_object_pose(params["object_name"], params.get("return_bbox_extent") == "true")

    api._get = get
    position, quaternion, size = api.get_object_pose("basket", return_bbox_extent=True)
    np.testing.assert_allclose(position, pose.p)
    np.testing.assert_allclose(quaternion, pose.q)
    np.testing.assert_allclose(size, [0.162, 0.162, 0.145])
    assert calls == [("/object_pose", {"object_name": "basket", "return_bbox_extent": "true"})]
    assert api.get_object_pose("basket")[2] is None


def test_empty_can_config_has_one_program_and_fixed_skill_summaries():
    from capx.envs.tasks.tactile.univtac_weight_classify import UniVTACEmptyCanSelectCodeEnv

    config = yaml.safe_load((CONFIG_ROOT / "can_empty_select/univtac_can_empty_select.yaml").read_text())
    assert config["env"]["cfg"]["apis"] == ["UniVTACTactileApi"]
    assert config["env"]["cfg"]["multi_turn_prompt"] is None
    assert config["multiturn_limit"] == 1
    assert config["trace_mode"] == "off"
    assert config["use_img_differencing"] is False
    assert not UniVTACEmptyCanSelectCodeEnv.tactile_skills_verbose
    assert not hasattr(UniVTACEmptyCanSelectCodeEnv, "oracle_code")
    library = default_library()
    names = UniVTACEmptyCanSelectCodeEnv.tactile_skills
    assert len(names) == len(set(names)) == 16
    assert set(names) <= {skill.name for skill in library.for_task("weight_classify")}
    assert not set(names) & {"grip-force-by-mass", "weigh-grasp-by-lift", "lift-follow-height"}
    env = object.__new__(UniVTACEmptyCanSelectCodeEnv)
    env.cfg = SimpleNamespace(deliver_tactile_skills=True)
    block = env._tactile_skill_block()
    assert "## How" not in block
    assert "## Traps" not in block
    for name in names:
        skill = library.get(name)
        assert f"- {name}: {skill.summary}" in block
        assert f"- {name}: {skill.objects}" in block
    for value in ("0.4 px", "0.2 px", "0.05 px", "0.7-1.0", "0.72-0.99", "180 g", "1.8 g"):
        assert value not in block
    env.cfg.deliver_tactile_skills = False
    assert env._tactile_skill_block() == ""

LINK = re.compile(r"\[\[([^\]]+)\]\]")

# The optional-paragraph marker an API docstring used to carry.  Deleted along
# with the mechanism that honoured it; this test is what keeps it deleted.
SECTION_MARKER = "[section:"

# The skills every task must receive, whichever type it belongs to: they are
# the ones whose absence would make a reading meaningless rather than merely
# less precise.
CORE = {
    "depth-grip-scale",
    "contact-flag-is-range",
    "one-call-approach",
    "reflex-stop-on-events",
    # grasp-verification is not core: its 28.3 / ~1 px close test was measured
    # on blind_pick's firm search grasp and rejects valid light squeezes (weight
    # weighing grip 0.64/0.67 px, hardness 0.10 close 0.85/1.02 px), so it is
    # routed to blind_pick by task name instead.
    "tactile-cannot-locate",
}


def _task_names() -> set[str]:
    """Task names as the configs define them — the routing key."""
    names = set()
    for cfg in sorted(CONFIG_ROOT.glob("*/low_level_*.yaml")):
        data = yaml.safe_load(cfg.read_text()) or {}
        names.add(str(data["task_name"]))
    return names


@pytest.fixture(scope="module")
def library() -> TactileSkillLibrary:
    return default_library()


# --------------------------------------------------------------------------
# 1. The taxonomy matches reality
# --------------------------------------------------------------------------


def test_every_task_in_the_configs_is_in_the_taxonomy(library) -> None:
    assert set(library.task_types.values())
    routed = {t for tasks in library.task_types.values() for t in tasks}
    assert routed == _task_names()


def test_taxonomy_covers_every_task_type(library) -> None:
    assert set(library.task_types) == {
        "classify", "force", "insert", "manipulate", "screw", "wipe"}


def test_no_task_is_in_two_types(library) -> None:
    routed = [t for tasks in library.task_types.values() for t in tasks]
    assert len(routed) == len(set(routed))


# --------------------------------------------------------------------------
# 2. Routing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_type",
                         ["classify", "force", "insert", "manipulate", "screw", "wipe"])
def test_core_skills_reach_every_type(library, task_type: str) -> None:
    tasks = library.task_types[task_type]
    for task in tasks:
        delivered = {s.name for s in library.for_task(task)}
        # A task can be held back from a core skill, but only on purpose: the
        # skill file itself has to name that task in not_tasks.  A skill that
        # simply stops reaching a task still fails here.
        for name in CORE - delivered:
            assert task in library.get(name).not_tasks, (task, name)
    # ...and each core skill still has to reach the type somewhere, or the
    # opt-out has quietly become an opt-out for the whole type.
    for name in CORE:
        assert any(
            name in {s.name for s in library.for_task(t)} for t in tasks
        ), (task_type, name)


def test_routing_by_task_adds_to_routing_by_type(tmp_path: Path) -> None:
    """`tasks:` names one task inside a type that does not all need the skill."""
    _write_library(
        tmp_path,
        GOOD.replace(
            "task_types: [classify]",
            "task_types: [classify]\ntasks: [bulb_tightening]",
        ),
    )
    lib = TactileSkillLibrary.load(tmp_path)
    skill = lib.get("sample")
    assert skill in lib.for_task("weight_classify")
    assert skill in lib.for_task("bulb_tightening")
    assert skill not in lib.for_task_type("screw")  # type routing is separate
    assert skill in lib.for_task_type("classify")


def test_not_tasks_takes_a_skill_back_out(tmp_path: Path) -> None:
    """`not_tasks:` is the one way routing loses a skill, and it is explicit.

    The skill stays routed to the type; one task is named out of it because on
    that task the skill's premise is false, not because it is unimportant.
    """
    _write_library(
        tmp_path,
        GOOD.replace(
            "task_types: [classify]",
            "task_types: [classify, screw]\nnot_tasks: [bulb_tightening]",
        ),
    )
    lib = TactileSkillLibrary.load(tmp_path)
    skill = lib.get("sample")
    assert skill in lib.for_task("weight_classify")
    assert skill not in lib.for_task("bulb_tightening")
    assert skill in lib.for_task_type("screw")  # type routing is untouched


def test_unknown_task_and_type_raise(library) -> None:
    with pytest.raises(KeyError):
        library.for_task("no_such_task")
    with pytest.raises(KeyError):
        library.for_task_type("no_such_type")
    with pytest.raises(KeyError):
        library.get("no-such-skill")


# --------------------------------------------------------------------------
# 3. Hygiene
# --------------------------------------------------------------------------


def test_every_skill_reaches_some_task(library) -> None:
    """An unrouted skill is dead weight: nothing would ever read it.

    Everything in this library is delivered to a prompt, so a skill the
    taxonomy cannot reach is a file that only costs maintenance.
    """
    reached = set()
    for tasks in library.task_types.values():
        for task in tasks:
            reached |= {s.name for s in library.for_task(task)}
    orphans = {s.name for s in library} - reached
    assert not orphans, sorted(orphans)


def _references(skill) -> list[str]:
    """Links anywhere in a skill — the body, or the note saying why to trust it."""
    return LINK.findall(f"{skill.summary}\n{skill.evidence}\n{skill.body}")


def test_cross_references_resolve(library) -> None:
    for skill in library:
        for target in _references(skill):
            assert target in library, f"{skill.name} links to unknown {target!r}"


def test_families_are_the_declared_ones(library) -> None:
    assert {s.family for s in library} == set(FAMILIES)


def test_every_skill_has_evidence_and_a_summary(library) -> None:
    for skill in library:
        assert skill.evidence, skill.name
        assert skill.summary and skill.summary[0].isupper(), skill.name
        assert skill.body.strip(), skill.name


# --------------------------------------------------------------------------
# 4. The delivered block stays cheap
# --------------------------------------------------------------------------


def test_compact_block_is_one_line_per_skill_and_within_budget(library) -> None:
    for tasks in library.task_types.values():
        for task in tasks:
            skills = library.for_task(task)
            block = library.render_for_task(task)
            lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
            assert len(lines) == len(skills)
            # One line each, and each line has to earn its place: this block is
            # added to a prompt whose budget is already spent elsewhere.  The
            # per-skill bound is the one that does the work; the total is a
            # backstop against a type quietly growing to cover the whole
            # library.  Measured 2026-10-07 after the merge to 52 skills, the
            # task-PROMPT trim, and the summary repairs five live evals forced
            # (the shear axis, the leg acceptance bar, the wrist budget, the
            # lead search's refusal, the wind-back's call, the call that
            # wind-back must not be made with, and the floor a stepping loop
            # has to end at) — plus what the sixth eval's verdict forced: the
            # reversible probe it never opened, and the hold that submits the
            # episode; and what the seventh forced: the over-feed belongs to
            # the lead block alone, a gate that fails has to say "missing"
            # rather than zero, and the bulb's own scene fact.  The later
            # rounds added two more of the same kind, both read off the last
            # submitted policy rather than guessed: the shear's pixels only
            # become a length through a gain that lived in a body, and the
            # wind-back needs its stopping condition next to its procedure,
            # because releasing a seated part is what threw the torque away.
            # Those are also
            # the summaries that had to grow past a line's worth, each because
            # the extra words name the thing not to do rather than a second
            # rule:
            # weight_classify 616 words over 31 skills, tactile_memory_match
            # 618/31, bulb_tightening 710/31, insert_hole 526/26, lift_can
            # 505/25 — about 20 words a line, name included, and one line at 55
            # because the run before it filled a gated-out leg's reading with a
            # zero and turned the loosening way, where the one before that
            # carried a procedure without its stopping condition; the gate
            # itself then grew eight words to say what it does not cover,
            # after a run threw the episode away at the first grasp because a
            # close had not read like a slide.  (Before the
            # one-line rewrite: 1225/902/818/736.  At 60 skills, before the
            # merge: 647/37, 557/31, 497/28, 451/26.)  Every
            # type routes 48-60% of the library, so the totals are a record
            # rather than a target; turning the hook on for a task means
            # accepting its block.  The lever is the summary: a rule and its
            # threshold, not the reasoning that produced it.  Baselines and the
            # checks a machine cannot do: capx/tactile_skills/MAINTENANCE.md.
            # The 2026-10-09 round is the one that moved the per-skill bound
            # from 25 to 26, and it moved it for the screw block alone: a run
            # took the direction from a comparison of the two legs' sizes and
            # turned the whole episode loosening, so that line has to say the
            # comparison is signed, and the other has to say what a wrecked
            # lead reading is re-taken on rather than clamped to.  Both are
            # words naming what not to do, and every other type sits near 20 a
            # line, well inside either bound — bulb_tightening 662/26 was the
            # only block within a word of it.
            # The second round that day spent the rest of that headroom on the
            # wind-back line, 41 -> 50 words, for the same kind of reason: a
            # trial ran three and a half hours re-grasping after every single
            # block with the bulb already in and the torque never arriving,
            # because the line could be read as "wind back between blocks" and
            # its stopping condition left the torque to a later block than the
            # one that read the seat.  So it now names the cadence (three or
            # four blocks per grasp, not one) and the endgame (the seat's
            # reading stops the re-setting, the torque ends the turn).
            # The round after that came from outside the library: the task's
            # acceptance bar moved from a torque band to the part being all the
            # way in, which deleted the endgame rule and, with it, the protocol
            # skill that carried the submission gesture (53 -> 52 skills).  The
            # line's tail is now the same action the task asks for — once
            # seated, stop — because there is nothing after the seat to turn
            # for.  bulb_tightening 642/26.
            # The first run under that bar (2026-10-09, evening) filled the two
            # gaps it could not measure — an 8 mm a turn lead and a +1
            # direction of its own, after its own coherence gate rejected every
            # reading it took — and unscrewed the bulb into the glass, so the
            # gate's line now says what a rejected reading is replaced by.  It
            # cost four words, not a new skill: the general rule already had a
            # home, and the two act lines keep their own instances of it.
            # bulb_tightening 646/26.
            # The next run (2026-10-10) followed the direction recipe exactly
            # as it stood, and died on it: the two legs were compared by the
            # shear field itself, which is counted from the reset baseline, so
            # both carried the same accumulated sink and six legs never
            # separated by the 0.15 mm the pair has to; and the widening had no
            # stop, its legs carrying half the probe's over-feed, which the
            # servo turns into descent — the ninth twist met the 128 N guard.
            # Three lines moved and the totals did not: the direction line gave
            # back four words while taking the change-not-level rule, the shear
            # line took seven to say the same thing at its own home, and the
            # bulb's grasp line lost three it did not need.
            # bulb_tightening 646/26.
            # The next run (2026-10-10) died at the other end of the range: at
            # seed 1's 4.36 mm a turn its 30° first block asked the lattice for
            # 0.55 mm of slide — the limit itself — so the whole ladder (30, 30,
            # 20, 15, 10°) read at or above the feed and the run filled the gap
            # with 5 mm a turn of its own.  Then, after a re-grasp whose pads
            # read under the gate, its retry loop re-drove the fed block twice
            # more and the third met the servo's limit and latched, the bulb
            # 2.67 mm in.  So the lead line now takes a 15° first block (0.13 to
            # 0.28 mm across the range), the gate line says a rejected reading
            # is taken again and never re-driven, and the re-grasp line fires on
            # a gate that keeps rejecting.  Five summaries moved; the net is one
            # word down.  bulb_tightening 645 words over 25 skills.
            # The round after that one (2026-10-10) was not eval-driven at all:
            # it came from reading the environment's own expert back.  The
            # expert feeds its two direction legs at 6.15 mm a turn and reads
            # the pair with no gate whatever -- on runs that finished SUCCESS
            # it read -13.5 against -2.9 px and -19.2 against -1.5 px on a pad
            # sitting at 0.24 to 0.31 coherence -- while this library had been
            # turning the legs in place, where a baseline-relative coherence
            # reads low because the pad has barely moved since the reset, and
            # then gating them at 0.70, so every pair came back short of two
            # real readings and runs filled the gap with a written-in
            # direction.  So the legs now carry a ~6 mm feed and no gate, the
            # gate's line says it is for a small signal and names the seat's
            # 0.04 mm slide as the one it belongs to, and the wind-back line
            # says which way round it goes -- a run had turned its counter the
            # other way and driven an empty hand 100 degrees deeper into the
            # twist it was trying to release.  Three summaries moved, net nine
            # words up, and the longest line fell from 56 to 52.
            # bulb_tightening 648 words over 25 skills, two inside the bound.
            # The next run (2026-10-10, the first under that library) took the
            # direction correctly and then stopped the insertion itself, three
            # blocks in: the seat line said the slide "reaching a tenth" of the
            # block's descent, which reads as a ceiling, and the gate's line
            # handed over a flat 0.04 mm -- a seat measured on a 0.356 mm block
            # -- so the rule it wrote was `abs(slide) <= min(0.1 * descent,
            # 0.04)` and it fired on the third of the six blocks the travel
            # needs, at 3.85 mm of the 4.50 the verdict wants and 0.026 N of
            # seat force.  The expert's own criterion is three things at once,
            # all three missing from the delivered lines: the slide signed (a
            # seat runs the way the glass does not), the tenth a floor rather
            # than a ceiling, and the call armed only after the twists have
            # sunk the hand 5.5 mm -- that run's hand stood at 5.02 mm, so the
            # arm alone would have blocked it.  Coherence was the other half:
            # 0.51, 0.31 and 0.32 at its three blocks, with the 0.70 the prompt
            # promises ("both thresholds are in the skills below") never
            # delivered at all.  So the seat line now carries the sign, the
            # floor and the arm, and the gate line carries that number.  Two
            # summaries moved, the net four words down.
            # bulb_tightening 644 words over 25 skills.
            # The next run (2026-10-11) was the first one under the seat fix,
            # and it passed -- the direction came off the legs, fifteen blocks
            # ran with no failure and the verdict cleared all eight conditions
            # -- but not one of the three readings it was meant to make was
            # actually made.  Its feed carried the twist's sign, so the lead
            # probe's overshoot came back positive, at or above its own feed,
            # on all four attempts, and the run fell back on a written-in 8 mm
            # a turn; and with the servo lifting the hand under every block
            # the seat's slide never read negative once, so the call that
            # ended the run was the API's own `reached`.  The sign the library
            # had left to the reader is the docstring's now, because it is a
            # property of feed_lead_m rather than a task rule, and the two
            # arithmetic lines say their terms are in mm -- the run's seat
            # test had compared a slide in mm against a tenth of a descent in
            # metres, so it fired on any negative reading at all.
            # bulb_tightening 647 words over 25 skills, three inside the bound.
            # The same day's next round carried the feed's own coupling into
            # the seat line, because that is the one thing neither the sign nor
            # the arm covers: the tenth and the arm are both read off the feed,
            # and a feed an eighth over the thread clears the tenth on every
            # block with nothing under the bulb -- the shape that run would
            # have taken had only its sign been wrong.  Sixteen tokens in,
            # nine of them taken back from other lines: the lattice line says
            # "one grasp's" where it said "counted from the grasp's start"
            # (four), and the wind-back line gives up a tail the task prompt
            # already carries, "the part is in" (five).  Seven net, against
            # three words of headroom, so the per-skill bound moves 26 to 27: a
            # screw block with two words to spare is not headroom, and every
            # other type sits near 20 a line, well inside either bound.
            # bulb_tightening 654 words over 25 skills, 21 inside the bound.
            # Two rounds after that one (both 2026-10-11) are recorded in
            # MAINTENANCE.md alone (第三十一组, 第三十二组): they moved the
            # gate to the seat's line, where the criteria actually read it,
            # and off the probe's and the legs' lines -- 654 to 673, then
            # back to 651, where the gate is stated exactly once.
            # This round (the 33rd) came from a survey of the delivered
            # surface rather than from a run.  Six edits.  The
            # sample_grasp_pose docstring said its first-call caveat was
            # "below" when the caveat sits above it.  The differential line
            # drops a 34.0 mm tail that belongs to a hardness pair, which is
            # a classified-block routine -- screw blocks get the line and
            # have no loose close to check (the body keeps it).  The gate's
            # line routes to screw only: every incident behind it is this
            # task, the gate exists in no other task's criteria, and no
            # other block's summaries introduce a reading that can be
            # rejected, so there the concept was an orphan.  The task prompt
            # stops counting the seat's conditions ("two conditions ... both
            # thresholds") now that the seat carries three numbers.  The
            # close line's mechanism is corrected against the server code:
            # with no force_threshold the close does take a default -- the
            # robot's own 27.5 mm depth, with no servo behind it -- so it
            # lands a few millimetres deeper than 27.2 does and never at the
            # same depth twice, rather than the "fingers run to their limit"
            # the line used to claim; the 27.2 action is unchanged and now
            # rests on the landing rather than on grip.  And the lattice
            # line stops saying "about 1 mm; past half of it", two budgets
            # in one sentence, and restates the body's own rule instead: a
            # few tenths readable, a sign beyond, and the reading never says
            # which.
            # bulb_tightening 660 words over 25 skills, 15 inside the bound.
            # The 34th round came from a run rather than from a survey, and
            # from the one thing a cadence cannot see: the budget is the twist
            # *not wound back*, and the lead block rides the same grasp as the
            # working blocks.  The run put a 15° lead block and four 30° blocks
            # on one grasp, scheduled its wind-back after every fourth
            # *working* block, and was refused at 132.45° of un-wound twist --
            # its plan was 135°, past the third of a turn the task prompt warns
            # about, and the counter meant to catch it started counting after
            # the lead block.  Three runs that passed the same day never held
            # more than about 91° on a grasp.  So the line stops naming a
            # cadence ("three or four blocks") and names the ledger instead:
            # the whole twist still on the wrist, the lead block included,
            # given back before the next block would take it past a third of a
            # turn.  Three words in; the body's own cadence is corrected with
            # it (three blocks when the lead rides the grasp, four when it does
            # not), and the evidence carries the datum.
            # bulb_tightening 663 words over 25 skills, 12 inside the bound.
            assert len(block.split()) < 27 * len(skills), (task, len(block.split()))
            assert len(block.split()) < 800, (task, len(block.split()))


def test_full_render_is_much_larger_than_compact(library) -> None:
    """The prose is for a reader who came for that one skill; a prompt is not."""
    for task in ("weight_classify", "bulb_tightening"):
        compact = len(library.render_for_task(task).split())
        full = len(library.render_for_task(task, compact=False).split())
        assert full > compact * 5, (task, compact, full)


def test_no_docstring_section_markers_survive() -> None:
    """The `[section:tag]` mechanism is gone; a stray marker would now be
    delivered to the LLM verbatim, since nothing strips it any more."""
    offenders = []
    # Only the code that builds a prompt: this test file and the inventory doc
    # name the marker on purpose, to say that it is gone.
    for root in ("capx", "env_configs"):
        for path in (REPO_ROOT / root).rglob("*"):
            if path.suffix not in (".py", ".md", ".yaml", ".yml"):
                continue
            if SECTION_MARKER in path.read_text(errors="ignore"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, offenders


def test_provenance_is_kept_out_of_the_delivered_form(library) -> None:
    for tasks in library.task_types.values():
        for task in tasks:
            block = library.render_for_task(task)
            assert "Written today in" not in block
            assert "Evidence:" not in block


# --------------------------------------------------------------------------
# 5. The prompt hook
# --------------------------------------------------------------------------


class _StubTask:
    """Just enough of an env to exercise the hook without building one."""

    _task_name = CodeExecutionEnvBase._task_name
    _reads_the_gel = CodeExecutionEnvBase._reads_the_gel
    _tactile_skill_block = CodeExecutionEnvBase._tactile_skill_block

    def __init__(self, task: str, **attrs) -> None:
        self.cfg = SimpleNamespace(low_level=SimpleNamespace(task_name=task))
        for key, value in attrs.items():
            setattr(self, key, value)


def test_a_shipped_config_resolves_its_task_name() -> None:
    """Through the configs themselves, because the stub above is the one shape
    production never builds.

    A shipped config hands `cfg.low_level` over as the *path* to the low-level
    YAML, not as a parsed dict -- so an implementation that read
    `cfg.low_level.task_name` found nothing, the name resolved to "", and the
    block reached no trial at all while every stub-based test here passed.
    """
    from capx.envs.configs.instantiate import instantiate as cfg_instantiate
    from capx.envs.configs.loader import DictLoader

    configs = sorted(CONFIG_ROOT.glob("*/univtac_*.yaml"))
    assert len(configs) >= 30, len(configs)  # a broken glob must not pass

    for cfg_path in configs:
        envcfg = cfg_instantiate(DictLoader.load(str(cfg_path))["env"]["cfg"])
        stub = _StubTask.__new__(_StubTask)
        stub.cfg = envcfg
        low = DictLoader.load(envcfg.low_level)
        assert stub._task_name() == low["task_name"], cfg_path.name
        assert default_library().task_type_of(stub._task_name()), cfg_path.name


def test_a_task_outside_the_taxonomy_gets_nothing() -> None:
    """Default-on means every UniVTAC task, not every task in the framework."""
    assert _StubTask("libero_90")._tactile_skill_block() == ""
    assert _StubTask("")._tactile_skill_block() == ""


def test_hook_routes_by_default() -> None:
    block = _StubTask("weight_classify")._tactile_skill_block()
    assert block.startswith("TACTILE SKILLS (routed to weight_classify, type classify):")
    assert "gel-shear-weight" in block
    assert "mirrored-shear-slide" not in block  # screw only


def test_hook_takes_explicit_names() -> None:
    block = _StubTask(
        "lift_can", tactile_skills=("depth-grip-scale",)
    )._tactile_skill_block()
    assert "depth-grip-scale" in block
    assert "gel-shear-weight" not in block


def test_an_empty_tuple_turns_the_block_off() -> None:
    assert _StubTask("weight_classify", tactile_skills=())._tactile_skill_block() == ""


def test_the_global_switch_turns_every_skill_off() -> None:
    """The trace experiment withholds the whole block, not just task-specific
    recipes, so a prompt can be run without any procedural knowledge."""
    stub = _StubTask("weight_classify")
    stub.cfg.deliver_tactile_skills = False
    assert stub._tactile_skill_block() == ""


def test_the_global_switch_beats_an_explicit_list() -> None:
    """A task that names skills by hand still receives none when the run has
    asked for none -- otherwise the block would be half present."""
    stub = _StubTask("weight_classify", tactile_skills=("depth-grip-scale",))
    stub.cfg.deliver_tactile_skills = False
    assert stub._tactile_skill_block() == ""


class _API:
    """Enough of an API object to answer what the hook asks it."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names

    def functions(self) -> dict:
        return dict.fromkeys(self._names)


def test_a_task_that_cannot_read_the_gel_gets_nothing() -> None:
    """The ablation configs keep the task name and swap the API for the
    motion-only one, so routing has to look at what the task can actually do.
    """
    motion = _API(("goto_pose", "move_delta", "get_object_pose"))
    assert _StubTask("lift_can", _apis={"motion": motion})._tactile_skill_block() == ""

    tactile = _API(("goto_pose", "get_tactile_data"))
    block = _StubTask("lift_can", _apis={"tactile": tactile})._tactile_skill_block()
    assert "lift-before-lateral" in block


def test_naming_skills_by_hand_skips_the_tactile_check() -> None:
    """An explicit list is a deliberate choice, so it is honoured as written."""
    motion = _API(("goto_pose",))
    block = _StubTask(
        "lift_can", _apis={"motion": motion}, tactile_skills=("depth-grip-scale",)
    )._tactile_skill_block()
    assert "depth-grip-scale" in block


def test_hook_refuses_an_unknown_skill() -> None:
    with pytest.raises(KeyError, match="unknown skill"):
        _StubTask("lift_can", tactile_skills=("nope",))._tactile_skill_block()


def test_verbose_switches_to_the_bodies() -> None:
    stub = _StubTask("bulb_tightening", tactile_skills=True)
    assert "## How" not in stub._tactile_skill_block()
    verbose = _StubTask(
        "bulb_tightening", tactile_skills=True, tactile_skills_verbose=True
    )._tactile_skill_block()
    assert "## How" in verbose
    assert "[[" not in verbose, "library cross-references leaked into the prompt"


def test_the_complete_prompt_carries_the_block_after_the_apis() -> None:
    """The hook has to reach the string the LLM is actually sent."""
    _get_complete_prompt = CodeExecutionEnvBase._get_complete_prompt

    class _Env(_StubTask):
        _apis: dict = {}
        _task_prompt = "Do the thing."

    routed = _get_complete_prompt(_Env("bulb_tightening"))
    assert routed.startswith("Do the thing.\nAPIs:\n\n\nTACTILE SKILLS")
    assert "lead-by-overshoot" in routed
    assert "gel-shear-weight" not in routed  # classify only

    off = _get_complete_prompt(_Env("bulb_tightening", tactile_skills=()))
    assert off == "Do the thing.\nAPIs:\n"


def test_the_only_hook_override_is_the_perception_bulb_variant() -> None:
    """The taxonomy is the routing table, with three deliberate exceptions.

    The bulb's perception config must not be handed `bulb-grasp-pose`: that
    skill's recipe is the server's ground-truth grasp branch
    (`_GT_GRASP_RULES`), and the perception API class switches that branch off
    (`_ground_truth_grasp = False`), so on that line it names a call that
    returns the hand's own parked pose.  Its class names the library's bulb
    routing minus that one skill by hand.

    The piano (2026-11-06) names exactly one skill.  Its index gel is real,
    but the task is a single-finger key press: none of the force family's
    grasp / carry / load rules describes it, so the class names the one rule
    written for it instead of letting type-routing hand it the strap and bulb
    recipes.

    The trace experiment (2026-11-08) names the frozen original-21 library's
    route for each task, matching the sibling study's initial delivery.  The
    old library carries the task's judgment constants; what the trace is meant
    to teach is the procedure around them (grasp, transport, placement,
    wrist reset, search).

    Nothing else opts out or names skills, so another override appearing here
    is a decision, not a drift.
    """
    from capx.envs.tasks import tactile as tactile_tasks
    import importlib
    import pkgutil

    declared = {}
    for mod in pkgutil.iter_modules(tactile_tasks.__path__):
        if not mod.name.startswith("univtac_"):
            continue
        module = importlib.import_module(f"{tactile_tasks.__name__}.{mod.name}")
        for name, obj in vars(module).items():
            if isinstance(obj, type) and hasattr(obj, "tactile_skills"):
                declared[name] = obj.tactile_skills

    assert set(declared) == {
        "UniVTACBulbTighteningPerceptionCodeEnv",
        "UniVTACBulbTighteningTraceCodeEnv",
        "UniVTACArmShadowPianoCodeEnv",
        "UniVTACArmShadowPianoTraceCodeEnv",
        "UniVTACBlindPickBallsTraceCodeEnv",
        "UniVTACGraspClassifyTraceCodeEnv",
        "UniVTACHardnessClassifyTraceCodeEnv",
        "UniVTACTactileMemoryMatchTraceCodeEnv",
        "UniVTACTensionStrapTraceCodeEnv",
        "UniVTACWeightClassifyTraceCodeEnv",
        "UniVTACEmptyCanSelectCodeEnv",
    }, declared
    assert declared["UniVTACArmShadowPianoCodeEnv"] == (
        "piano-gel-force-band",), declared["UniVTACArmShadowPianoCodeEnv"]
    piano_block = _StubTask(
        "arm_shadow_piano", tactile_skills=("piano-gel-force-band",)
    )._tactile_skill_block()
    assert "piano-gel-force-band" in piano_block, piano_block
    assert "bulb-close-force" not in piano_block, piano_block

    # The trace arms deliver the frozen original-21 library's route for their
    # task -- the same initial delivery as the sibling study
    # (capx/tactile_skills/initial_libraries/original-21-3758efb).  What the
    # trace is meant to teach is the procedure around those readings.
    trace_names = set(declared["UniVTACGraspClassifyTraceCodeEnv"])
    assert trace_names == {
        "depth-grip-scale",
        "follow-is-signal",
        "gel-shear-weight",
        "position-mode-for-yield",
        "shear-vs-displacement",
        "texture-slope-two-grips",
    }, trace_names
    trace_block = _StubTask(
        "grasp_classify",
        tactile_skills=tuple(trace_names),
    )._tactile_skill_block()
    assert "texture-slope-two-grips" in trace_block
    assert "force_threshold 27.5 and 20.5" in trace_block
    assert "split 0.06" in trace_block, "the frozen split must survive"
    assert "depth-grip-scale" in trace_block
    assert "[[" not in trace_block, "unresolvable cross-references reached the prompt"

    # The task prompt must say the run is unattended: without it the model
    # asked the operator for calibration slopes and blocked the chain.  The
    # placement promise is rewritten because the old library has no place
    # family.
    from capx.envs.tasks.tactile.univtac_grasp_classify import (
        UniVTACGraspClassifyTraceCodeEnv,
    )

    trace_prompt = UniVTACGraspClassifyTraceCodeEnv.prompt
    assert "unattended" in trace_prompt
    assert "input()" in trace_prompt
    assert "The placement recipe" not in trace_prompt
    assert "worked out from your attempts" in trace_prompt

    # weight_classify keeps the old library's seven: the weight split, the
    # weigh-by-lift protocol, the shear channel and the shared classify rules.
    # Its prompt's placement promise is rewritten for the same reason.
    from capx.envs.tasks.tactile.univtac_weight_classify import (
        PROMPT_TRACE as WEIGHT_PROMPT_TRACE,
        UniVTACWeightClassifyTraceCodeEnv,
    )

    weight_names = set(declared["UniVTACWeightClassifyTraceCodeEnv"])
    assert weight_names == {
        "depth-grip-scale",
        "follow-is-signal",
        "gel-shear-weight",
        "grip-force-by-mass",
        "position-mode-for-yield",
        "shear-vs-displacement",
        "weigh-grasp-by-lift",
    }, weight_names
    weight_block = _StubTask(
        "weight_classify", tactile_skills=tuple(weight_names)
    )._tactile_skill_block()
    assert "split 0.2 px" in weight_block, "the frozen weight split must survive"
    assert "weigh-grasp-by-lift" in weight_block
    assert "[[" not in weight_block
    assert "unattended" in WEIGHT_PROMPT_TRACE and "input()" in WEIGHT_PROMPT_TRACE
    assert "The placement recipe" not in WEIGHT_PROMPT_TRACE
    assert "worked out from your attempts" in WEIGHT_PROMPT_TRACE

    # hardness_classify keeps the old library's six, including its 1.2 mm
    # two-close split.
    from capx.envs.tasks.tactile.univtac_hardness_classify import (
        PROMPT_TRACE as HARDNESS_PROMPT_TRACE,
        UniVTACHardnessClassifyTraceCodeEnv,
    )

    hardness_names = set(declared["UniVTACHardnessClassifyTraceCodeEnv"])
    assert hardness_names == {
        "depth-grip-scale",
        "follow-is-signal",
        "gel-shear-weight",
        "hardness-two-closes",
        "position-mode-for-yield",
        "shear-vs-displacement",
    }, hardness_names
    hardness_block = _StubTask(
        "hardness_classify", tactile_skills=tuple(hardness_names)
    )._tactile_skill_block()
    assert "split 1.2" in hardness_block, "the frozen hardness split must survive"
    assert "[[" not in hardness_block
    assert "unattended" in HARDNESS_PROMPT_TRACE and "input()" in HARDNESS_PROMPT_TRACE
    assert "The placement recipe" not in HARDNESS_PROMPT_TRACE
    assert "worked out from your attempts" in HARDNESS_PROMPT_TRACE

    # arm_shadow_piano names exactly the one skill the frozen library has for
    # it; the taxonomy's force route is bypassed by the class, as the
    # library's own comment says.
    from capx.envs.tasks.tactile.univtac_arm_shadow_piano import (
        PROMPT_TRACE as PIANO_PROMPT_TRACE,
        UniVTACArmShadowPianoTraceCodeEnv,
    )

    assert declared["UniVTACArmShadowPianoTraceCodeEnv"] == (
        "piano-gel-force-band",), declared["UniVTACArmShadowPianoTraceCodeEnv"]
    piano_trace_block = _StubTask(
        "arm_shadow_piano", tactile_skills=("piano-gel-force-band",)
    )._tactile_skill_block()
    assert "0.35 N light and 0.50 N heavy" in piano_trace_block
    assert "[[" not in piano_trace_block
    assert "unattended" in PIANO_PROMPT_TRACE and "input()" in PIANO_PROMPT_TRACE
    assert UniVTACArmShadowPianoTraceCodeEnv.prompt_template is PIANO_PROMPT_TRACE

    # memory_match keeps six of the old library's seven: the one-grasp
    # protocol, its 0.02 texture split, the depth scale, the load channel and
    # the servo caveat.  ``gel-shear-weight`` is the deliberate deviation --
    # this task's weight axis is follow/sag, never shear, so the old library's
    # rule would contradict ``follow-is-signal`` in the same block.
    from capx.envs.tasks.tactile.univtac_tactile_memory_match import (
        PROMPT_TRACE,
        UniVTACTactileMemoryMatchTraceCodeEnv,
    )

    mm_names = set(declared["UniVTACTactileMemoryMatchTraceCodeEnv"])
    assert mm_names == {
        "can-texture-difference",
        "depth-grip-scale",
        "follow-is-signal",
        "memory-match-one-grasp",
        "position-mode-for-yield",
        "shear-vs-displacement",
    }, mm_names
    mm_block = _StubTask(
        "tactile_memory_match", tactile_skills=tuple(mm_names)
    )._tactile_skill_block()
    assert "0.02" in mm_block, "the frozen split must survive"
    assert "0.50" in mm_block and "0.25" in mm_block, "the one-grasp protocol must survive"
    assert "gel-shear-weight" not in mm_block, "the contradicting weight rule leaked"
    assert "world-frame-transport" not in mm_block
    assert "[[" not in mm_block, "unresolvable cross-reference reached the prompt"
    assert "unattended" in PROMPT_TRACE and "input()" in PROMPT_TRACE
    assert "one-grasp protocol" in PROMPT_TRACE

    # tension_strap keeps the gauge and the two control recipes, as the frozen
    # library does; the old library has no approach, grip or recovery rule.
    from capx.envs.tasks.tactile.univtac_tension_strap import (
        PROMPT_TRACE as STRAP_PROMPT_TRACE,
        UniVTACTensionStrapTraceCodeEnv,
    )

    strap_names = set(declared["UniVTACTensionStrapTraceCodeEnv"])
    assert strap_names == {
        "depth-grip-scale",
        "shear-vs-displacement",
        "strap-grasp-and-ramp",
        "strap-hold-creeps",
        "strap-load-is-shear-difference",
    }, strap_names
    strap_block = _StubTask(
        "tension_strap", tactile_skills=tuple(strap_names)
    )._tactile_skill_block()
    assert "0.93" in strap_block, "the gauge must keep its calibration"
    assert "strap-grasp-and-ramp" in strap_block
    assert "strap-hold-creeps" in strap_block
    assert "one-call-approach" not in strap_block
    assert "[[" not in strap_block
    assert "unattended" in STRAP_PROMPT_TRACE and "input()" in STRAP_PROMPT_TRACE
    # The trace class must re-render its own wording, not the base template.
    assert UniVTACTensionStrapTraceCodeEnv.prompt_template is STRAP_PROMPT_TRACE

    # bulb_tightening keeps the old library's six: the direction legs, the
    # shear-to-mm conversion, the lead formula, the seat rule, the depth scale
    # and the channel rule.  The wrist-reset recipe is not in the old library,
    # so that one prompt promise stays rewritten.
    from capx.envs.tasks.tactile.univtac_bulb_tightening import (
        PROMPT_TRACE as BULB_PROMPT_TRACE,
        UniVTACBulbTighteningTraceCodeEnv,
    )

    bulb_names = set(declared["UniVTACBulbTighteningTraceCodeEnv"])
    assert bulb_names <= set(default_library().skills), bulb_names - set(default_library().skills)
    assert bulb_names == {
        "depth-grip-scale",
        "lead-by-overshoot",
        "mirrored-shear-slide",
        "seat-by-slide-collapse",
        "shear-vs-displacement",
        "thread-direction-by-legs",
    }, bulb_names
    bulb_block = _StubTask(
        "bulb_tightening", tactile_skills=tuple(bulb_names)
    )._tactile_skill_block()
    assert "lead-by-overshoot" in bulb_block
    assert "seat-by-slide-collapse" in bulb_block
    assert "thread-direction-by-legs" in bulb_block
    assert "mirrored-shear-slide" in bulb_block
    assert "18 px/mm" in bulb_block, "the channel calibration must survive"
    assert "wrist-limit-segmentation" not in bulb_block
    assert "bulb-close-force" not in bulb_block
    assert "27.2" not in bulb_block, "the grasp recipe is not in the old library"
    assert "[[" not in bulb_block
    assert "unattended" in BULB_PROMPT_TRACE and "input()" in BULB_PROMPT_TRACE
    # The direction, lead and seat promises are true; the wrist-reset promise
    # is not, and stays rewritten.
    assert "The skills below give the method" in BULB_PROMPT_TRACE
    assert "the tactile skills at the end of this prompt" in BULB_PROMPT_TRACE
    assert "how to re-set it, and when to stop, are yours to" in BULB_PROMPT_TRACE
    assert "the skills below give the reset sequence" not in BULB_PROMPT_TRACE
    assert "when to stop re-setting" not in BULB_PROMPT_TRACE
    assert UniVTACBulbTighteningTraceCodeEnv.prompt is BULB_PROMPT_TRACE

    # blind_pick_balls keeps the old library's four: the depth scale, the
    # channel rule, the material reading with its 0.06 bar, and the wide-pad
    # fact.  Search, transport and placement are not in the old library.
    from capx.envs.tasks.tactile.univtac_blind_pick_balls import (
        PROMPT_TRACE as BALLS_PROMPT_TRACE,
        UniVTACBlindPickBallsTraceCodeEnv,
    )

    balls_names = set(declared["UniVTACBlindPickBallsTraceCodeEnv"])
    assert balls_names <= set(default_library().skills), balls_names - set(default_library().skills)
    assert balls_names == {
        "depth-grip-scale",
        "shear-vs-displacement",
        "texture-in-relief",
        "wide-pads-see-nothing",
    }, balls_names
    balls_block = _StubTask(
        "blind_pick_balls", tactile_skills=tuple(balls_names)
    )._tactile_skill_block()
    assert "texture-in-relief" in balls_block
    assert "0.06" in balls_block, "the frozen material bar must survive"
    assert "search-continues-after-miss" not in balls_block
    assert "lift-before-lateral" not in balls_block
    assert "world-frame-transport" not in balls_block
    assert "z≈0.28" not in balls_block, "the cruise height leaked"
    assert "wide-pads-see-nothing" in balls_block
    assert "[[" not in balls_block
    assert "unattended" in BALLS_PROMPT_TRACE and "input()" in BALLS_PROMPT_TRACE
    assert UniVTACBlindPickBallsTraceCodeEnv.prompt is BALLS_PROMPT_TRACE

    named = set(declared["UniVTACBulbTighteningPerceptionCodeEnv"])
    routed = {s.name for s in default_library().for_task("bulb_tightening")}
    assert named <= routed, named - routed
    assert routed - named == {"bulb-grasp-pose"}, routed - named

    # And through the hook: the skill and the call it names are both gone.
    block = _StubTask("bulb_tightening", tactile_skills=tuple(named))._tactile_skill_block()
    assert "bulb-grasp-pose" not in block
    assert "sample_grasp_pose" not in block
    assert "lead-by-overshoot" in block  # the rest of the bulb set is still there


def test_no_task_class_prunes_the_api_doc() -> None:
    """`api_doc_sections` is gone: one API doc goes to every task."""
    import capx.envs.tasks.tactile as tactile_tasks

    assert not hasattr(CodeExecutionEnvBase, "api_doc_sections")
    for name in dir(tactile_tasks):
        if name.endswith("CodeEnv"):
            assert not hasattr(getattr(tactile_tasks, name), "api_doc_sections"), name


# --------------------------------------------------------------------------
# 6. Malformed input fails loudly
# --------------------------------------------------------------------------


def _write_library(root: Path, skill_text: str, taxonomy: str | None = None) -> None:
    (root / "skills" / "sense").mkdir(parents=True, exist_ok=True)
    (root / "taxonomy.yaml").write_text(
        taxonomy
        or "task_types:\n  classify:\n    - weight_classify\n  screw:\n    - bulb_tightening\n"
    )
    (root / "skills" / "sense" / "sample.md").write_text(skill_text)


GOOD = """---
id: S0
name: sample
family: sense
summary: A sample skill.
task_types: [classify]
---

## How

Nothing.
"""


def test_a_good_minimal_library_loads(tmp_path: Path) -> None:
    _write_library(tmp_path, GOOD)
    lib = TactileSkillLibrary.load(tmp_path)
    assert list(lib.skills) == ["sample"]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t.replace("task_types: [classify]", "task_types: [clasify]"), "unknown task type"),
        (lambda t: t.replace("task_types: [classify]", "task_types: [classify]\ntasks: [weight_classfy]"), "unknown task"),
        (lambda t: t.replace("task_types: [classify]", "task_types: [classify]\nnot_tasks: [weight_classfy]"), "unknown task in not_tasks"),
        (lambda t: t.replace("task_types: [classify]", "task_types: [classify]\ntasks: [weight_classify]\nnot_tasks: [weight_classify]"), "both tasks and not_tasks"),
        (lambda t: t.replace("name: sample", "name: sample2"), "file stem"),
        (lambda t: t.replace("family: sense", "family: reading"), "family"),
        (lambda t: t.replace("summary: A sample skill.\n", ""), "missing"),
        (lambda t: t.replace("task_types: [classify]", "task_types: []"), "names no task type"),
        (lambda t: t.replace("family: sense", "family: sense\ncolour: blue"), "unknown frontmatter keys"),
    ],
)
def test_a_malformed_skill_fails_loudly(tmp_path: Path, mutate, message: str) -> None:
    _write_library(tmp_path, mutate(GOOD))
    with pytest.raises(SkillError, match=message):
        TactileSkillLibrary.load(tmp_path)


def test_duplicate_skill_names_fail(tmp_path: Path) -> None:
    _write_library(tmp_path, GOOD)
    (tmp_path / "skills" / "act").mkdir(parents=True)
    (tmp_path / "skills" / "act" / "sample.md").write_text(
        GOOD.replace("family: sense", "family: act")
    )
    with pytest.raises(SkillError, match="duplicate skill name"):
        TactileSkillLibrary.load(tmp_path)


def test_taxonomy_typos_fail(tmp_path: Path) -> None:
    _write_library(
        tmp_path,
        GOOD,
        taxonomy="task_types:\n  classify:\n    - weight_classify\n  screw:\n    - weight_classify\n",
    )
    with pytest.raises(SkillError, match="is in two types"):
        TactileSkillLibrary.load(tmp_path)


def test_the_shipped_library_is_the_one_under_test(library) -> None:
    """Guard against the tests passing on a stale copy elsewhere on the path."""
    assert library.root == LIBRARY_ROOT
    assert len(library) >= 52
