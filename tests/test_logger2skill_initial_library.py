from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from capx.logger2skill import storage
from capx.logger2skill.initial_library import (
    INITIAL_METADATA, build_initial, check_frozen_metadata, common_view, verify_source)
from capx.logger2skill.experiment import load_suite, run_loop, verify_gpu_process
from capx.logger2skill.storage import LibraryStore, digest, read_json, render_memory, validate_library

SOURCE = Path(__file__).resolve().parents[1] / "capx/tactile_skills/initial_libraries/original-21-3758efb"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def source():
    if not SOURCE.exists():
        pytest.skip("Frozen upstream source not installed")
    return SOURCE


@pytest.fixture
def suite():
    return load_suite(REPO / "env_configs/logger2skill/weight_initial21_pilot.yaml")


def new_store(path):
    if hasattr(storage, "MARKDOWN_FORMAT"):
        return LibraryStore(path, storage.MARKDOWN_FORMAT)
    return LibraryStore(path)


@pytest.mark.parametrize("unexpected", [False, True])
def test_graphics_context_isolation_checked_before_model_request(tmp_path, monkeypatch, unexpected):
    xml = ('<nvidia_smi_log><gpu><uuid>GPU-one</uuid><processes><process_info>'
           '<pid>123</pid><type>C+G</type><used_memory>8000 MiB</used_memory>'
           '</process_info></processes></gpu><gpu><uuid>GPU-zero</uuid><processes>'
           '<process_info><pid>' + ('123' if unexpected else '456') +
           '</pid><type>G</type><used_memory>6 MiB</used_memory>'
           '</process_info></processes></gpu></nvidia_smi_log>')
    monkeypatch.setattr("capx.logger2skill.experiment.subprocess.check_output", lambda *a, **k: xml)
    if unexpected:
        with pytest.raises(RuntimeError, match="GPU isolation"):
            verify_gpu_process(123, "GPU-one", tmp_path)
    else:
        verify_gpu_process(123, "GPU-one", tmp_path)


@pytest.mark.parametrize("other_type", ["G", "C+G"])
def test_explicit_auxiliary_graphics_permission_does_not_allow_other_compute(tmp_path, monkeypatch, other_type):
    xml = ('<nvidia_smi_log><gpu><uuid>GPU-one</uuid><processes><process_info>'
           '<pid>123</pid><type>C+G</type><used_memory>8000 MiB</used_memory>'
           '</process_info></processes></gpu><gpu><uuid>GPU-zero</uuid><processes>'
           '<process_info><pid>123</pid><type>' + other_type +
           '</type><used_memory>6 MiB</used_memory>'
           '</process_info></processes></gpu></nvidia_smi_log>')
    monkeypatch.setattr("capx.logger2skill.experiment.subprocess.check_output", lambda *a, **k: xml)
    if other_type == "G":
        verify_gpu_process(123, "GPU-one", tmp_path, allow_auxiliary_graphics=True)
    else:
        with pytest.raises(RuntimeError, match="GPU isolation"):
            verify_gpu_process(123, "GPU-one", tmp_path, allow_auxiliary_graphics=True)


def modify(candidate):
    if (candidate / "skill.md").exists():
        path = candidate / "skill.md"
        path.write_text("CPU fixture update.\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path = candidate / "skills/act/strap-grasp-and-ramp.md"
        text = path.read_text(encoding="utf-8")
        path.write_text(text + "\nCPU fixture evidence.\n", encoding="utf-8")


def test_real_source_views_are_equal_nonempty_and_complete(source, tmp_path):
    original = digest(source)
    manifest = verify_source(source)
    assert manifest["skill_count"] == 21
    assert len(manifest["external_document_references"]) == 16
    for mode in ("capx", "skillopt_markdown"):
        build_initial(source, tmp_path / mode, mode, ("weight_classify",))
    first = common_view(tmp_path / "capx", "weight_classify", 12000)
    second = common_view(tmp_path / "skillopt_markdown", "weight_classify", 12000)
    assert first == second
    assert first.count("\n- ") == 7
    assert "0.2" in first and "28.5" in first and "0.05" in first
    assert len(first) < 12000
    for entry in manifest["files"]:
        assert (tmp_path / "capx" / entry["path"]).read_bytes() == (source / entry["path"]).read_bytes()
        assert (source / entry["path"]).read_text(encoding="utf-8") in (tmp_path / "skillopt_markdown/skill.md").read_text(encoding="utf-8")
    assert digest(source) == original


def test_checksums_detect_modified_initial_source(source, tmp_path):
    local = tmp_path / "source"
    shutil.copytree(source, local)
    path = local / "skills/act/strap-grasp-and-ramp.md"
    path.write_text(path.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        verify_source(local)


def test_native_format_noop_update_and_old_active_isolation(source, tmp_path):
    store = new_store(tmp_path / "library")
    initial = store.initialize(source, ("weight_classify",))
    initial_path = store.path(initial)
    before = render_memory(initial_path, "weight_classify", 12000)
    candidate = tmp_path / "candidate"
    shutil.copytree(initial_path, candidate)
    validate_library(candidate)
    assert store.adopt(candidate, initial, "unchanged") == initial
    modify(candidate)
    assert common_view(candidate, "weight_classify", 12000) is None
    updated = store.adopt(candidate, initial, "updated")
    assert updated != initial
    assert digest(initial_path) == initial["sha256"]
    assert render_memory(initial_path, "weight_classify", 12000) == before
    if (candidate / "skill.md").exists():
        assert "CPU fixture update." in render_memory(store.path(updated), "weight_classify", 12000)
    assert store.initialize(source, ("weight_classify",)) == updated
    assert render_memory(store.path(updated), "arm_shadow_piano", 12000) == ""
    assert "LICENSE" not in render_memory(initial_path, "weight_classify", 12000)


def test_unregistered_reference_and_metadata_edits_rejected(source, tmp_path):
    store = new_store(tmp_path / "library")
    pointer = store.initialize(source, ("weight_classify",))
    candidate = tmp_path / "candidate"
    shutil.copytree(store.path(pointer), candidate)
    path = candidate / ("skill.md" if (candidate / "skill.md").exists() else "skills/act/strap-grasp-and-ramp.md")
    old = path.read_bytes()
    path.write_bytes(old + b"\n[[unregistered-new-document]]\n")
    with pytest.raises(ValueError, match="Unresolved"):
        store.adopt(candidate, pointer, "bad")
    assert store.active() == pointer
    path.write_bytes(old)
    (candidate / INITIAL_METADATA).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen initial"):
        store.adopt(candidate, pointer, "changed_policy")
    assert store.active() == pointer


def test_existing_empty_library_not_silently_replaced(source, tmp_path):
    store = new_store(tmp_path / "library")
    empty = store.initialize()
    with pytest.raises(ValueError, match="different initial source"):
        store.initialize(source, ("weight_classify",))
    assert store.active() == empty


def test_loop_stops_on_infrastructure_without_curating_or_repeating(source, suite, tmp_path):
    calls = []
    def execute(config, task, seed, output, memory, timeout):
        calls.append(seed)
        output.mkdir(parents=True)
        return {"case": {"case_id": f"{task}_{seed}", "task": task, "seed": seed, "final_success": None},
                "infrastructure_error": "server startup failed", "public_case_exported": False}
    state = run_loop(suite, tmp_path, execute, curate=lambda *a: pytest.fail("Must not call curator"))
    assert state["status"] == "infrastructure_error"
    assert state["metrics"]["native_failures"] == 0
    assert state["metrics"]["task_attempts"] == 0
    assert state["metrics"]["sr_all_attempts"] is None
    assert calls == [44000]
    assert state["rounds"][0]["episodes"][0]["library_after"]["version"] == "initial"
    with pytest.raises(RuntimeError, match="no automatic retry"):
        run_loop(suite, tmp_path, execute)


def test_loop_uses_updated_version_and_failed_update_retains_it(source, suite, tmp_path):
    loaded = []
    def execute(config, task, seed, output, memory, timeout):
        output.mkdir(parents=True)
        loaded.append((seed, memory.name, render_memory(memory, task, 12000)))
        return {"case": {"case_id": f"{task}_{seed}", "task": task, "seed": seed,
                         "final_success": False}, "public_case_exported": True}
    def curate(config, output, case, store, before, timeout):
        after = before
        if case["seed"] == 44000:
            candidate = output / "candidate"
            shutil.copytree(store.path(before), candidate)
            modify(candidate)
            after = store.adopt(candidate, before, "accepted_44000")
        return {"status": "adopted" if after != before else "failed",
                "library_before": before, "library_after": after}
    state = run_loop(suite, tmp_path, execute, curate)
    assert state["status"] == "complete"
    assert [row[1] for row in loaded] == ["initial", "accepted_44000", "accepted_44000"]
    assert len(state["metrics"]["per_task"]["weight_classify"]["history"]) == 3
