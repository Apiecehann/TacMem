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

from capx.envs.tasks.base import CodeExecEnvConfig, CodeExecutionEnvBase
from capx.integrations.base_api import ApiBase
from capx.tactile_skills import (
    FAMILIES,
    SkillError,
    TactileSkillLibrary,
    default_library,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPO_ROOT / "env_configs" / "univtac"
LIBRARY_ROOT = REPO_ROOT / "capx" / "tactile_skills"

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
    "grasp-verification",
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
    assert set(library.task_types) == {"classify", "insert", "manipulate", "screw", "wipe"}


def test_no_task_is_in_two_types(library) -> None:
    routed = [t for tasks in library.task_types.values() for t in tasks]
    assert len(routed) == len(set(routed))


# --------------------------------------------------------------------------
# 2. Routing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", ["classify", "insert", "manipulate", "screw", "wipe"])
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


class _API(ApiBase):
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
    """The taxonomy is the routing table, with one deliberate exception.

    The bulb's perception config must not be handed `bulb-grasp-pose`: that
    skill's recipe is the server's ground-truth grasp branch
    (`_GT_GRASP_RULES`), and the perception API class switches that branch off
    (`_ground_truth_grasp = False`), so on that line it names a call that
    returns the hand's own parked pose.  Its class names the library's bulb
    routing minus that one skill by hand; nothing else opts out or names
    skills, so a second override appearing here is a decision, not a drift.
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

    assert set(declared) == {"UniVTACBulbTighteningPerceptionCodeEnv"}, declared

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


@pytest.mark.parametrize("names", ["get_ee_pose", [1], ["move_delta", "move_delta"], ["_get"], ["not_a_tool"]])
def test_invalid_native_function_selection_is_rejected(names) -> None:
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi

    api = UniVTACTactileApi(None)
    before = api.selected_functions()
    with pytest.raises((TypeError, ValueError)):
        api.configure_functions(names)
    assert api.selected_functions() == before


def test_function_selection_defaults_and_documentation() -> None:
    from omegaconf import OmegaConf
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi

    api = UniVTACTactileApi(None)
    assert api.selected_functions() == api.functions()
    assert "submit_episode" not in api.selected_functions()
    api.configure_functions(OmegaConf.create(["get_tactile_image", "get_ee_pose", "move_delta"]))
    selected = api.selected_functions()
    assert set(selected) == {"get_tactile_image", "get_ee_pose", "move_delta", "check_success"}
    for name, fn in selected.items():
        assert fn == getattr(api, name)
    assert set(re.findall(r"^([a-z_]+)\(", api.combined_doc(), re.M)) == set(selected) - api._prompt_hidden
    api.configure_functions([])
    assert set(api.selected_functions()) == {"check_success"}
    assert api.combined_doc() == ""


@pytest.mark.parametrize("name", ["get_object_pose", "sample_grasp_pose", "rotate_gripper"])
def test_ball_function_selection_cannot_restore_withdrawn_tools(name) -> None:
    from capx.integrations.tactile.univtac_ball_api import UniVTACBallApi

    api = UniVTACBallApi(None)
    with pytest.raises(ValueError, match="Unknown functions"):
        api.configure_functions([name])
    api.configure_functions(["get_ee_pose", "get_tactile_image"])
    assert api.selected_functions()["get_ee_pose"] == api.get_ee_pose


@pytest.mark.parametrize("selection", [[], {"OtherApi": ["get_ee_pose"]}])
def test_api_function_configuration_rejects_wrong_api_mapping(selection) -> None:
    with pytest.raises((TypeError, ValueError), match="api_functions"):
        CodeExecutionEnvBase(CodeExecEnvConfig(low_level=SimpleNamespace(), apis=[], api_functions=selection))


@pytest.mark.parametrize("task", ["weight_classify", "grasp_classify", "hardness_classify", "tactile_memory_match", "bulb_tightening", "blind_pick_balls", "tension_strap"])
def test_seven_native_yamls_bind_the_documented_functions(task, monkeypatch) -> None:
    from capx.envs.configs.instantiate import instantiate
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi
    from capx.integrations.tactile.univtac_ball_api import UniVTACBallApi

    monkeypatch.chdir(REPO_ROOT)
    factories = {"UniVTACTactileApi": UniVTACTactileApi, "UniVTACBallApi": UniVTACBallApi}
    monkeypatch.setattr("capx.envs.tasks.base.get_api", factories.__getitem__)
    path = CONFIG_ROOT / ("tension_strap_smoke.yaml" if task == "tension_strap" else f"{task}/univtac_{task}.yaml")
    config = yaml.safe_load(path.read_text())
    env = instantiate(config["env"])
    name = "UniVTACBallApi" if task == "blind_pick_balls" else "UniVTACTactileApi"
    assert env.cfg.apis == [name]
    assert env.low_level_env.task_name == task
    api = env._apis[name]
    selected = api.selected_functions()
    assert set(selected) == set(config["env"]["cfg"]["api_functions"][name]) | {"check_success"}
    assert set(re.findall(r"^([a-z_]+)\(", api.combined_doc(), re.M)) == set(selected) - api._prompt_hidden
    for fn_name, fn in selected.items():
        expected = api.close_gripper_position if fn_name == "close_gripper" else getattr(api, fn_name)
        assert env._exec_globals[fn_name] == fn == expected
    assert "get_ee_pose" in selected
    raw = config["agent0_mode"] == "raw"
    assert ("get_tactile_data" in selected) is raw
    assert ("get_tactile_image" in selected) is raw
    assert ("get_tactile_depth" in selected) is raw
    assert "detect_slip" not in selected
    block = env._tactile_skill_block()
    for name in config["env"]["cfg"]["tactile_skills"]:
        assert default_library().get(name).summary in block
    assert "No marker coordinates" in env._task_prompt if raw else "No fingertip sensor observations" in env._task_prompt
    if task == "blind_pick_balls":
        assert not {"get_object_pose", "sample_grasp_pose", "rotate_gripper"} & selected.keys()
    if task == "tension_strap":
        assert env.cfg.append_final_check == "submit_episode"
        assert "submit_episode(" not in api.combined_doc()
        assert config["api_servers"][0]["task"] == task
        assert config["api_servers"][0]["seed"] == config["trials"] == config["resume_idx"]
    else:
        assert env.final_check_name() == "check_success"
    env._init_exec_globals()
    result = env._exec_user_code("print('native print preserved')")
    assert result["ok"] and "native print preserved" in result["stdout"]
    for fn_name, fn in selected.items():
        assert env._exec_globals[fn_name] == fn


def test_selected_native_reads_and_motion_keep_http_contract(monkeypatch) -> None:
    import base64
    import io
    import numpy as np
    from PIL import Image
    from capx.integrations.tactile import univtac_api

    api = univtac_api.UniVTACTactileApi(None, server_url="http://native.test")
    api.configure_functions(["get_tactile_image", "get_ee_pose", "get_tactile_data", "move_delta"])
    rgb = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    payload = {f"{side}_{key}": value for side in ("left", "right") for key, value in {
        "contact": True, "depth_mm": 27.5, "marker_displacement_px": 0.2,
        "marker_coherence": 0.6, "marker_anisotropy": 1.1, "height_roughness_mm": 0.03,
        "rgb_base64": base64.b64encode(buf.getvalue()).decode("ascii")}.items()}
    pose = {"position": [0.4, 0.1, 0.3], "quaternion_wxyz": [1, 0, 0, 0]}
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dict(pose if url.endswith("/ee_pose") else payload))
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"success": True, "tactile": payload})
    monkeypatch.setattr(univtac_api.requests, "get", get)
    monkeypatch.setattr(univtac_api.requests, "post", post)
    selected = api.selected_functions()
    for side in ("left", "right"):
        np.testing.assert_array_equal(selected["get_tactile_image"](side), rgb)
    assert selected["get_ee_pose"]() == pose
    native_data = selected["get_tactile_data"]()
    assert native_data["left_depth_mm"] == 27.5
    assert native_data["right_height_roughness_mm"] == 0.03
    assert selected["move_delta"](dx=0.01) == univtac_api._trim_motion_tactile(payload)
    assert calls[-1][0] == "http://native.test/move_delta"
    assert calls[-1][1]["json"] == {"dx": 0.01, "dy": 0.0, "dz": 0.0, "drx": 0.0, "dry": 0.0, "drz": 0.0, "coord": "world", "tag": "move_delta"}
    payload.pop("left_rgb_base64")
    assert selected["get_tactile_image"]("left") is None


def _native_submission_namespace():
    import ast
    import logging
    import traceback
    from dataclasses import dataclass
    from typing import Any

    path = REPO_ROOT / "capx" / "serving" / "launch_univtac_server.py"
    names = {"TaskBacking", "AVAILABLE_TASKS", "TASK_BACKING", "DEFAULT_BACKING", "_backing_for", "_sim_submit_episode"}
    nodes = []
    for node in ast.parse(path.read_text()).body:
        bound = {node.name} if isinstance(node, (ast.ClassDef, ast.FunctionDef)) else set()
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        if isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if bound & names:
            nodes.append(node)
    ns = {"__name__": __name__, "dataclass": dataclass, "Path": Path, "Any": Any,
          "VITAFORGE_ROOT": Path("/native/opentac"), "UNIVTAC_ROOT": Path("/native/univtac"),
          "logger": logging.getLogger(__name__), "traceback": traceback}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec", dont_inherit=True), ns)
    return ns


def test_tension_native_registration_selects_task_profile() -> None:
    ns = _native_submission_namespace()
    assert "tension_strap" in ns["AVAILABLE_TASKS"]
    backing = ns["_backing_for"]("tension_strap")
    assert backing.root == ns["VITAFORGE_ROOT"]
    assert backing.config_file == "tension_strap.yml"
    assert backing.cfg_overrides == ()


@pytest.mark.parametrize("task, method", [("tension_strap", "finish_episode"), ("bulb_tightening", "_finish_bulb_episode")])
def test_native_end_calls_only_the_tasks_own_finalizer(task, method) -> None:
    from unittest.mock import Mock

    ns = _native_submission_namespace()
    finish = Mock()
    verdict = {"completed": False, "check_early_stop": True, "detail": "native verdict"}
    ns.update(_TASK_NAME=task, _TASK=SimpleNamespace(**{method: finish}), _sim_check_success=Mock(return_value=verdict))
    assert ns["_sim_submit_episode"]() == verdict
    finish.assert_called_once_with()
    ns["_sim_check_success"].assert_called_once_with()
    finish.side_effect = RuntimeError("native finalizer failed")
    failure = ns["_sim_submit_episode"]()
    assert failure["completed"] is False
    if task == "tension_strap":
        assert failure["success"] is False
    ns["_sim_check_success"].assert_called_once_with()


def test_hidden_end_submission_does_not_return_the_verdict(monkeypatch, capsys) -> None:
    from capx.integrations.tactile import univtac_api

    api = univtac_api.UniVTACTactileApi(None, server_url="http://native.test")
    api.configure_functions(["submit_episode"])
    response = {"completed": True, "detail": {"private": "not returned"}}
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: response)
    monkeypatch.setattr(univtac_api.requests, "post", post)
    assert api.selected_functions()["submit_episode"]() is None
    assert calls[0][0] == "http://native.test/submit_episode"
    assert calls[0][1]["json"] == {}
    assert api.combined_doc() == ""
    assert capsys.readouterr().out == ""
    response.update(success=False, message="native finalizer failed")
    with pytest.raises(RuntimeError, match="native finalizer failed"):
        api.selected_functions()["submit_episode"]()


def test_agent0_three_raw_channels_round_trip_and_no_substitution(tmp_path, monkeypatch):
    import json
    import numpy as np
    import torch
    from capx.serving import launch_univtac_server as server
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi

    depth = np.array([[24.125, 31.333334, 34.0], [26.765432, np.nan, 28.000002]], dtype=np.float32)
    rgb = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
    marker = 255 - rgb
    class Sensor:
        def __init__(self, missing=False):
            self.values = {"rgb": None if missing else torch.from_numpy(rgb),
                           "rgb_marker": torch.from_numpy(marker), "depth": torch.from_numpy(depth)}
        def get_observations(self, channels):
            assert set(channels) <= {"rgb", "rgb_marker", "depth"}
            return {key: self.values[key] for key in channels}
    task = SimpleNamespace(step_count=3, sim=SimpleNamespace(current_time_step_index=12, current_time=0.05),
                           _tactile_manager=SimpleNamespace(tactiles={"left_tactile": Sensor(), "right_tactile": Sensor(True)}))
    monkeypatch.setattr(server, "_TASK", task)
    monkeypatch.setattr(server, "_TASK_NAME", "weight_classify")
    monkeypatch.setattr(server, "_OBSERVATION_MODE", "raw")
    payload = server._raw_tactile_snapshot()
    assert payload["step"] == 12 and payload["time_seconds"] == 0.05
    api = UniVTACTactileApi(None)
    api.configure_observation_mode("raw", str(tmp_path))
    monkeypatch.setattr(api, "_get", lambda path: payload)
    raw = api.get_tactile_data()
    np.testing.assert_array_equal(raw["sensors"]["left"]["depth"], depth)
    np.testing.assert_array_equal(api.get_tactile_image("left", "rgb"), rgb)
    np.testing.assert_array_equal(api.get_tactile_image("left", "rgb_marker"), marker)
    assert api.get_tactile_image("right", "rgb") is None
    assert raw["sensors"]["right"]["depth_unit"] == "mm"
    api.get_tactile_data()
    records = [json.loads(line) for line in (tmp_path / "index.jsonl").read_text().splitlines()]
    assert len(records) == 2
    for row in records:
        value = np.load(tmp_path / row["files"]["depth"]["path"])
        np.testing.assert_array_equal(value, depth)
    assert not {"marker", "force", "contact", "left_depth_mm"} & set(raw)


@pytest.mark.parametrize("mode", ["raw", "none"])
def test_agent0_motion_position_control_and_status_only(mode, monkeypatch):
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi
    api = UniVTACTactileApi(None)
    api.configure_observation_mode(mode)
    calls = []
    def post(path, body=None):
        calls.append((path, body))
        return {"success": True, "step": 10, "tactile": {"left_depth_mm": 22, "force_N": 9}}
    monkeypatch.setattr(api, "_post", post)
    result = api.functions()["close_gripper"](position=0.3)
    assert calls[-1] == ("/gripper", {"action": "close", "use_adaptive_grasp": False, "position": 0.3})
    assert result == {"ok": True, "step": 10}
    assert "detect_slip" not in api.functions()
    if mode == "none":
        assert "get_tactile_data" not in api.functions()
        with pytest.raises(RuntimeError):
            api.get_tactile_data()
    with pytest.raises(ValueError):
        api.close_gripper(force_threshold=27.0)


def test_agent0_public_server_responses_and_logs_exclude_processed_fields(monkeypatch):
    import json
    from capx.serving import launch_univtac_server as server
    records = []
    monkeypatch.setattr(server, "_OBSERVATION_MODE", "none")
    monkeypatch.setattr(server, "_TASK", SimpleNamespace(step_count=1, sim=SimpleNamespace(current_time_step_index=4, current_time=0.01)))
    monkeypatch.setattr(server.action_log, "log_event", lambda evt, **fields: records.append({"evt": evt, **fields}))
    monkeypatch.setattr(server.action_log, "log_action", lambda a, args, **fields: records.append({"a": a, **fields}))
    monkeypatch.setattr(server, "_run_on_main", lambda fn, *args, **kw: fn(*args))
    def move():
        server._TASK.step_count = 3
        server._TASK.sim.current_time_step_index = 12
        return {"success": False, "message": "left_depth_mm=22 ground_truth=heavy", "tactile": {"force": 2}}
    result = server._run_action("move", {}, 1, move, detail_prefix="Move failed")
    text = json.dumps([result, records])
    assert "depth" not in text and "ground_truth" not in text and '"force"' not in text
    assert records[-1]["extra"]["physics_start"] == 4
    assert records[-1]["extra"]["physics_end"] == 12
    with pytest.raises(server.HTTPException) as error:
        server.get_tactile()
    assert error.value.status_code == 403
    with pytest.raises(server.HTTPException) as error:
        server.force_state()
    assert error.value.status_code == 403
    monkeypatch.setattr(server, "_run_on_main", lambda *a, **kw: {"head_rgb_base64": "ordinary", "tactile": {"contact": True}})
    assert json.loads(server.get_observation().body) == {"head_rgb_base64": "ordinary"}
    monkeypatch.setattr(server.action_log, "set_harness_mode", lambda: pytest.fail("Experiment action logging disabled"))
    monkeypatch.setattr(server, "_RECORDER", SimpleNamespace(set_harness_mode=lambda: None, harness_mode=True))
    server.set_recording_mode(server.RecordingModeRequest(mode="harness"))


@pytest.mark.parametrize("mode", ["raw", "none"])
def test_agent0_video_records_scene_without_sensor_panels(mode, monkeypatch):
    import base64
    import io
    import numpy as np
    import torch
    from PIL import Image
    from capx.serving import launch_univtac_server as server
    rgb = np.full((3, 4, 3), 82, dtype=np.uint8)
    task = SimpleNamespace(_update_render=lambda: None,
        _get_observations=lambda: {"observation": {"head": {"rgb": torch.from_numpy(rgb)}}},
        get_frame_shot=lambda obs: pytest.fail("Composite frame used"))
    monkeypatch.setattr(server, "_OBSERVATION_MODE", mode)
    monkeypatch.setattr(server, "_TASK", task)
    image = np.asarray(Image.open(io.BytesIO(base64.b64decode(server._capture_frame()))))
    np.testing.assert_array_equal(image, rgb)


def test_agent0_single_responses_request_has_image_usage_and_output_budget(tmp_path, monkeypatch):
    import json
    from capx.llm import client
    sent = []
    response = {"output": [{"content": [{"type": "output_text", "text": "print(1)"}]}],
                "usage": {"input_tokens": 1234, "output_tokens": 83}}
    class Session:
        trust_env = True
        def post(self, url, **kwargs):
            assert self.trust_env is False
            sent.append(kwargs)
            return SimpleNamespace(status_code=200, text=json.dumps(response), json=lambda: response, raise_for_status=lambda: None)
        def close(self):
            pass
    monkeypatch.setattr(client.requests, "Session", Session)
    args = client.ModelQueryArgs(model="gpt-6.1-sol", server_url="https://example.invalid/v1", max_tokens=8192)
    args.single_program = True
    args.disable_proxy = True
    args.max_query_retries = 0
    args.model_timeout_seconds = 300
    args.request_log_dir = str(tmp_path)
    prompt = [{"role": "user", "content": [{"type": "text", "text": "Code"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,TEST"}}]}]
    out = client.query_model(args, prompt)
    assert out["usage"] == response["usage"]
    assert sent[0]["timeout"] == 300
    assert sent[0]["json"]["max_output_tokens"] == 8192
    assert sent[0]["json"]["input"][0]["content"][1]["type"] == "input_image"
    assert client.is_vlm_model("gpt-6.1-sol")
    with pytest.raises(RuntimeError, match="budget"):
        client.query_model(args, prompt)
    assert len(sent) == 1
    assert json.loads((tmp_path / "response.json").read_text())["usage"] == response["usage"]


def test_agent0_failed_model_call_is_not_retried(tmp_path, monkeypatch):
    import requests
    from capx.llm import client
    calls = []
    class Session:
        def post(self, url, **kwargs):
            calls.append(url)
            def fail():
                raise requests.HTTPError("503")
            return SimpleNamespace(status_code=503, text='{"error":"busy"}', raise_for_status=fail)
        def close(self):
            pass
    monkeypatch.setattr(client.requests, "Session", Session)
    args = client.ModelQueryArgs(model="gpt-6.1-sol", server_url="https://example.invalid/v1")
    args.single_program = True
    args.max_query_retries = 0
    args.request_log_dir = str(tmp_path)
    with pytest.raises(requests.HTTPError):
        client.query_model(args, [{"role": "user", "content": [{"type": "text", "text": "code"}]}])
    assert len(calls) == 1 and (tmp_path / "request_status.json").exists()


def test_agent0_one_program_and_exception_does_not_regenerate(tmp_path, monkeypatch):
    from capx.utils.launch_utils import _single_program
    from capx.envs import runner
    assert _single_program("```python\nprint(1)\n```") == "print(1)"
    for invalid in ("", "```python\nprint(1)\n```\n```python\nprint(2)\n```", "not python prose"):
        with pytest.raises((ValueError, SyntaxError)):
            _single_program(invalid)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise ValueError("generation failed")
    monkeypatch.setattr(runner, "_wait_for_server_healthy", lambda env: None)
    monkeypatch.setattr(runner, "_run_single_trial", fail)
    cfg = {"agent0_mode": "none", "max_trial_attempts": 1, "output_dir": str(tmp_path)}
    summary = runner._run_trial_with_retries(None, 42000, SimpleNamespace(trial_timeout=3), cfg, "must not retry")
    assert len(calls) == 1 and not summary.success and summary.task_completed is None


def test_agent0_metrics_count_actual_requests_and_missing_usage(tmp_path):
    import json
    from capx.utils.launch_utils import TrialSummary, _agent0_metrics
    (tmp_path / "actions_sent.jsonl").write_text('\n'.join(json.dumps({"endpoint": "/move"}) for _ in range(3))+'\n')
    events = [
        {"evt": "action_begin", "a": "move", "action_id": 1},
        {"kind": "action", "a": "move", "action_id": 1, "physics_start": 5, "physics_end": 12, "ok": False},
        {"evt": "action_begin", "a": "reset", "action_id": 2},
        {"kind": "action", "a": "reset", "action_id": 2, "physics_start": 12, "physics_end": 3},
        {"evt": "query_span", "physics_start": 12, "physics_end": 14},
    ]
    (tmp_path / "univtac_actions.jsonl").write_text('\n'.join(map(json.dumps, events)))
    cfg = {"output_dir": str(tmp_path), "task_name": "weight_classify", "agent0_mode": "raw", "test_seeds": list(range(41000,41040)), "skills_sha256": "test", "seed_manifest_sha256": "test"}
    summary = TrialSummary(trial=41000, success=True, reward=0, terminated=False, truncated=False,
        sandbox_rc=0, log="", task_completed=False, num_code_blocks=1)
    metrics = _agent0_metrics(summary, cfg, 2.0)
    assert metrics["Execute_steps"] == 3 and metrics["physics_steps"] is None
    assert metrics["measured_physics_steps"] == 7 and not metrics["physics_steps_complete"]
    assert metrics["query_physics_steps"] == 2 and metrics["reset_physics_steps"] is None
    assert metrics["input_tokens"] is None and metrics["output_tokens"] is None
    assert metrics["task_completed"] is False and metrics["code_success"] is True
    (tmp_path / "actions_sent.jsonl").write_text(json.dumps({"endpoint": "/move"}) + '\n')
    assert _agent0_metrics(summary, cfg, 2.0)["physics_steps"] == 7
    (tmp_path / "model").mkdir()
    (tmp_path / "model/response.json").write_text(json.dumps({"usage":{"input_tokens":105,"output_tokens":44}}))
    events.append({"evt":"action_begin", "a":"gripper", "action_id":3})
    (tmp_path / "univtac_actions.jsonl").write_text('\n'.join(map(json.dumps, events)))
    metrics = _agent0_metrics(summary, cfg, 3.0)
    assert metrics["physics_steps"] is None and metrics["input_tokens"] == 105 and metrics["output_tokens"] == 44
    summary.success = False
    summary.num_code_blocks = 0
    summary.failure_phase = "generation"
    (tmp_path / "model/request_status.json").write_text(json.dumps({"completed": False, "error_type": "ConnectionError"}))
    assert _agent0_metrics(summary, cfg, 3.0)["failure_type"] == "infrastructure"
    (tmp_path / "model/request_status.json").write_text(json.dumps({"completed": True}))
    assert _agent0_metrics(summary, cfg, 3.0)["failure_type"] == "generation"


def test_agent0_sanitized_skill_selection_and_native_config_loading(tmp_path, monkeypatch):
    from capx.envs.launch import LaunchArgs
    from capx.utils.launch_utils import _load_config
    from capx.envs.configs.instantiate import instantiate
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setenv("CAPX_TASK_SEED", "41000")
    forbidden = re.compile(r"left_depth_mm|right_depth_mm|marker_|detect_slip|force_threshold|anisotrop|coherence|contact.flag|estimated_.*force", re.I)
    paths = [CONFIG_ROOT / f"{task}/univtac_{task}.yaml" for task in ("weight_classify","grasp_classify","hardness_classify","tactile_memory_match","bulb_tightening","blind_pick_balls")]
    paths.append(CONFIG_ROOT / "tension_strap_smoke.yaml")
    for path in paths:
        args = LaunchArgs(config_path=str(path), output_dir=str(tmp_path / path.stem))
        env_cfg, config, servers = _load_config(args)
        assert args.single_program and args.max_tokens == 8192 and args.max_query_retries == 0
        assert config["max_trial_attempts"] == 1 and servers[0]["seed"] == 41000
        assert config["skills_sha256"] and env_cfg["cfg"]["multi_turn_prompt"] == ""
        env = instantiate(env_cfg)
        assert not forbidden.search(env._tactile_skill_block())
        for name in env_cfg["cfg"]["tactile_skills"]:
            skill = default_library().get(name)
            assert skill.channels == () and not forbidden.search(skill.render(provenance=True))
        for api in env._apis.values():
            assert not forbidden.search(api.combined_doc())
        assert "{force_request}" not in env._task_prompt


def test_agent0_sent_action_count_includes_http_rejection_but_not_queries_or_end(tmp_path, monkeypatch):
    import json
    import requests
    from capx.integrations.tactile.univtac_api import UniVTACTactileApi
    monkeypatch.setenv("CAPX_RUN_DIR", str(tmp_path))
    api = UniVTACTactileApi(None)
    api.configure_observation_mode("none")
    def post(url, **kwargs):
        def reject():
            if url.endswith("/move_delta"):
                raise requests.HTTPError(response=SimpleNamespace(status_code=422, text='{"detail":"limit"}'))
        return SimpleNamespace(raise_for_status=reject, json=lambda: {"success": True})
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"position": [0,0,0], "quaternion_wxyz": [1,0,0,0]}))
    with pytest.raises(RuntimeError):
        api.move_delta(dx=10)
    api.open_gripper()
    api.get_ee_pose()
    api.submit_episode()
    sent = [json.loads(x) for x in (tmp_path / "actions_sent.jsonl").read_text().splitlines()]
    assert [x["endpoint"] for x in sent] == ["/move_delta", "/gripper"]
    assert not (tmp_path / "raw_tactile").exists()


def test_agent0_initial_scene_image_reaches_prompt_without_an_extra_model_call(monkeypatch):
    import numpy as np
    from capx.envs import trial
    image = np.full((3, 4, 3), 82, dtype=np.uint8)
    env = SimpleNamespace(render=lambda: image)
    obs = {"full_prompt": [{"role": "user", "content": [{"type": "text", "text": "place object"}]}]}
    config = {"use_visual_feedback": True, "use_img_differencing": False, "use_video_differencing": False}
    args = SimpleNamespace(model="gpt-6.1-sol")
    monkeypatch.setattr(trial, "_query_model", lambda *a, **kw: pytest.fail("Extra image-description request"))
    images, _, _ = trial._capture_initial_visual_feedback(env, obs, config, args, args)
    assert len(images) == 1
    assert obs["full_prompt"][-1]["content"][-1]["type"] == "image_url"
    assert obs["full_prompt"][-1]["content"][-1]["image_url"]["url"].startswith("data:image/")


def test_agent0_aggregate_is_native_success_and_incomplete_suite_stays_provisional(tmp_path):
    import json
    from capx.utils.launch_utils import _save_agent0_summary
    folder = tmp_path / "seed_41000" / "one"
    folder.mkdir(parents=True)
    row = {"seed": 41000, "split": "test", "task_completed": False, "code_success": True,
           "failure_type": "native_task_failure", "Execute_steps": 4, "physics_steps": 20,
           "input_tokens": None, "output_tokens": None, "elapsed_seconds": 5.0}
    (folder / "metrics.json").write_text(json.dumps(row))
    cfg = {"suite_dir": str(tmp_path), "test_seeds": list(range(41000, 41040))}
    _save_agent0_summary([], cfg)
    result = json.loads((tmp_path / "aggregate.json").read_text())
    assert result["success_count"] == 0 and result["success_rate"] is None and result["final"] is False
    assert len(result["not_run"]) == 39 and result["input_tokens"]["unknown_count"] == 1
    assert result["input_tokens"]["sum_known"] is None
    second = tmp_path / "seed_41000" / "two"
    second.mkdir()
    (second / "metrics.json").write_text(json.dumps({**row, "task_completed": True}))
    _save_agent0_summary([], cfg)
    result = json.loads((tmp_path / "aggregate.json").read_text())
    assert result["duplicate_seeds"] == [41000] and result["success_count"] == 0


def test_native_classify_profile_and_standalone_ball_defaults():
    ns = _native_submission_namespace()
    assert ns["_backing_for"]("grasp_classify").config_file == "gelsight.yml"
    assert ns["_backing_for"]("blind_pick_balls").config_file is None

def _shadow_server_namespace():
    import ast
    import io
    import base64
    import numpy as np
    from unittest.mock import Mock
    path = REPO_ROOT / "capx/serving/launch_univtac_server.py"
    names = {"_shadow_move", "_shadow_tactile", "_shadow_hand_state"}
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"np": np, "io": io, "base64": base64,
          "_SHADOW_SUBMITTED": False, "_record_frame": Mock(), "_physics_sample": Mock(return_value={"physics_step": 12, "time_seconds": .05})}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    return ns


def test_shadow_native_step_stops_on_termination_and_rejects_bounds():
    import numpy as np
    from unittest.mock import Mock
    ns = _shadow_server_namespace()
    task = SimpleNamespace(_done=False, action_space=SimpleNamespace(
        low=np.array([-.01,-.32,-.006,0.]), high=np.array([.09,.26,.05,.50])))
    def step(action):
        task._done = True
        return {"tactile": {"index": {"rgb": np.zeros((2,3,3),dtype=np.uint8)}}}, 99., True, False, {"success": True, "key_forces": [99.]}
    task.step = Mock(side_effect=step)
    ns.update(_TASK=task, _SHADOW_ACTION=np.zeros(4))
    for target in ([0.,0.,0.,.51], [float("nan"),0.,0.,0.], [0.,0.]):
        with pytest.raises(ValueError):
            ns["_shadow_move"](target, 2)
    task.step.assert_not_called()
    result = ns["_shadow_move"]([0.,0.,0.,.4], 5)
    assert result == {"success": True, "native_done": True}
    assert task.step.call_count == 1
    with pytest.raises(RuntimeError, match="ended"):
        ns["_shadow_move"](None, 1)
    task._done = False
    ns["_SHADOW_SUBMITTED"] = True
    with pytest.raises(RuntimeError, match="ended"):
        ns["_shadow_move"](None, 1)


def test_shadow_raw_observations_roundtrip_and_none_disabled(tmp_path):
    import base64
    import io
    import json
    import numpy as np
    from PIL import Image
    from capx.integrations.tactile.univtac_api import UniVTACShadowPianoApi
    rgb = np.arange(18,dtype=np.uint8).reshape(2,3,3)
    depth = np.array([[.02,.019,.018],[.017,.016,.015]],dtype="<f4")
    ns = _shadow_server_namespace()
    ns.update(Image=Image, _TASK=SimpleNamespace(tactile=SimpleNamespace(depth_m=depth)),
              _SHADOW_RGB=rgb, _OBSERVATION_MODE="raw")
    payload = ns["_shadow_tactile"]()
    api = UniVTACShadowPianoApi(None)
    api.configure_observation_mode("raw", str(tmp_path))
    api._get = lambda *args, **kwargs: payload
    result = api.get_digit_tactile()
    np.testing.assert_array_equal(result["depth"], depth)
    np.testing.assert_array_equal(result["rgb"], rgb)
    api.get_digit_tactile()
    records = [json.loads(line) for line in (tmp_path/"index.jsonl").read_text().splitlines()]
    assert len(records) == 1 and records[0]["depth_unit"] == "m"
    assert set(result) == {"sensor","rgb","depth","depth_unit","step","time_seconds"}
    api.configure_observation_mode("none")
    assert "get_digit_tactile" not in api.functions()
    with pytest.raises(RuntimeError):
        api.get_digit_tactile()
    ns["_OBSERVATION_MODE"] = "none"
    with pytest.raises(RuntimeError):
        ns["_shadow_tactile"]()


def test_shadow_hand_state_contains_no_key_or_score_fields():
    import numpy as np
    import torch
    ns = _shadow_server_namespace()
    data = SimpleNamespace(body_pos_w=torch.zeros(1,1,3), body_quat_w=torch.tensor([[[1.,0.,0.,0.]]]),
                           body_lin_vel_w=torch.zeros(1,1,3), joint_pos=torch.zeros(1,8), joint_vel=torch.zeros(1,8))
    ns["_TASK"] = SimpleNamespace(hand=SimpleNamespace(data=data), _palm=0, _index=7, _arm_ids=list(range(7)),
                                 _action_state=lambda: np.zeros(4), _done=False,
                                 cfg=SimpleNamespace(physics_dt=1/240, action_repeat=4))
    result = ns["_shadow_hand_state"]()
    assert result["control_dt_seconds"] == 1/60
    assert not ({"score","success","key_angles","key_forces","reward","notes_completed"} & set(result))

