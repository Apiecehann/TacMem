"""Tests for the optional tactile trace (capx.integrations.tactile.trace).

The trace is a side channel: off unless ``CAPX_TACTILE_TRACE`` names a path,
and never allowed to raise into a caller that is holding an arm.  Both
halves are covered here — the row writer on its own, and the two places in
:class:`UniVTACTactileApi` that call it (motion POSTs and ``/tactile``).

No server, no simulator: the HTTP layer is stubbed below ``_post``/``_get``
so the real recording call is exercised.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from unittest import mock

import pytest

from capx.integrations.tactile import trace
from capx.integrations.tactile.univtac_api import _MOTION_TACTILE_FIELDS, UniVTACTactileApi


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _readings() -> dict:
    """A full scalar payload — every field, so a dropped one is visible."""
    data = {field: 0.0 for field in _MOTION_TACTILE_FIELDS}
    data.update(
        left_contact=False,
        right_contact=True,
        left_marker_shear_dx=-0.31,
        left_marker_shear_dy=0.07,
        right_marker_shear_dx=0.29,
        right_marker_shear_dy=-0.06,
        left_marker_anisotropy=1.13,
        right_marker_anisotropy=0.97,
    )
    return data


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------


def test_unset_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv(trace.ENV_VAR, raising=False)
    trace.record("/tactile", _readings())
    assert list(tmp_path.iterdir()) == []


def test_file_path_appends_one_row_per_call(monkeypatch, tmp_path):
    target = tmp_path / "run.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", _readings())
    trace.record("/close_gripper", {"left_depth_mm": 27.9}, {"position": 0.6})
    rows = _rows(target)
    assert [row["call"] for row in rows] == ["/tactile", "/close_gripper"]
    assert rows[0]["readings"]["left_marker_shear_dx"] == -0.31
    assert rows[1]["args"] == {"position": 0.6}
    assert all(isinstance(row["t"], float) for row in rows)


def test_directory_path_gets_the_fixed_name(monkeypatch, tmp_path):
    # The directory does not exist yet on the first row of a trial.
    target = tmp_path / "trial_07"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", _readings())
    assert _rows(target / "tactile_trace.jsonl")


def test_images_are_dropped_but_scalars_survive(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    readings = {**_readings(), "left_rgb_base64": "A" * 4096,
                "right_depth_base64": "B" * 4096}
    trace.record("/tactile", readings)
    row = _rows(target)[0]
    assert not any(key.endswith("base64") for key in row["readings"])
    assert row["readings"]["right_marker_anisotropy"] == 0.97


def test_non_finite_becomes_null(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", {"a": math.nan, "b": math.inf, "c": -math.inf, "d": 1.5})
    text = target.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    assert _rows(target)[0]["readings"] == {"a": None, "b": None, "c": None, "d": 1.5}


def test_empty_readings_write_nothing(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/ee_pose", None)
    trace.record("/ee_pose", {})
    assert not target.exists()


def test_unwritable_destination_does_not_raise(monkeypatch, tmp_path):
    # A directory standing where the file should be: the write must fail
    # quietly rather than take the trial down with it.
    target = tmp_path / "taken.jsonl"
    target.mkdir()
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", _readings())


def test_environment_is_re_read_on_every_call(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.delenv(trace.ENV_VAR, raising=False)
    trace.record("/tactile", _readings())
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", _readings())
    assert len(_rows(target)) == 1


def test_rows_carry_the_trial_they_belong_to(monkeypatch, tmp_path):
    # One run writes one file, so the trial is a column.  Without it a
    # finished run cannot be split into the trials that worked and the ones
    # that did not, which is the only question the trace is mined for.
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    try:
        trace.set_trial(1)
        trace.record("/tactile", _readings())
        trace.set_trial(2)
        trace.record("/tactile", _readings())
        trace.set_trial(None)
        trace.record("/tactile", _readings())
    finally:
        trace.set_trial(None)
    assert [row["trial"] for row in _rows(target)] == [1, 2, None]


def test_rows_carry_the_run_they_belong_to(monkeypatch, tmp_path):
    # A re-run appends to the same file and repeats the same trial numbers,
    # so the trial column alone cannot say which attempt a row belongs to.
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    trace.record("/tactile", _readings())
    trace.record("/tactile", _readings())
    assert [row["run"] for row in _rows(target)] == [trace.RUN_ID, trace.RUN_ID]


def _run_id_from_a_fresh_process() -> str:
    """The id a process importing the module now would stamp on its rows.

    Loaded by path rather than through the package: the package pulls in the
    simulator, and the question here is only what the module computes for
    itself when a new process starts.
    """
    script = (
        "import importlib.util, sys;"
        "spec = importlib.util.spec_from_file_location('trace', sys.argv[1]);"
        "module = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(module);"
        "print(module.RUN_ID)"
    )
    done = subprocess.run([sys.executable, "-c", script, trace.__file__],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_a_restart_is_a_new_run_not_more_of_the_same_one():
    # Without this a second attempt at a seed would be read as more evidence
    # for the first attempt's outcome -- and if the two ended differently, as
    # one trial that both worked and did not.
    first = _run_id_from_a_fresh_process()
    second = _run_id_from_a_fresh_process()
    assert first and second
    assert first != second


# ---------------------------------------------------------------------------
# The client hooks
# ---------------------------------------------------------------------------


def _client() -> UniVTACTactileApi:
    return UniVTACTactileApi(object(), server_url="http://test:8120")


def _response(payload: dict) -> mock.Mock:
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_refused_motion_still_records_its_tactile_state(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    api = _client()
    payload = {"success": False, "message": "gripper blocked", "tactile": _readings()}
    with mock.patch("capx.integrations.tactile.univtac_api.requests.post",
                    return_value=_response(payload)):
        with pytest.raises(RuntimeError, match="gripper blocked"):
            api._post("/close_gripper", {"position": 0.6})
    # The row is written before the failure is raised: what the pads read at
    # the moment of refusal is the part worth keeping.
    row = _rows(target)[0]
    assert row["call"] == "/close_gripper"
    assert row["args"] == {"position": 0.6}
    assert row["readings"]["left_depth_mm"] == 0.0


def test_motion_without_tactile_payload_writes_nothing(monkeypatch, tmp_path):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    api = _client()
    with mock.patch("capx.integrations.tactile.univtac_api.requests.post",
                    return_value=_response({"success": True})):
        api._post("/reset")
    assert not target.exists()


def test_get_tactile_data_records_a_row_and_prints_every_scalar(monkeypatch, tmp_path, capsys):
    target = tmp_path / "t.jsonl"
    monkeypatch.setenv(trace.ENV_VAR, str(target))
    api = _client()
    with mock.patch("capx.integrations.tactile.univtac_api.requests.get",
                    return_value=_response(_readings())):
        api.get_tactile_data()
    row = _rows(target)[0]
    assert row["call"] == "/tactile"
    assert row["readings"]["left_marker_shear_dy"] == 0.07
    line = capsys.readouterr().out.strip()
    assert "L_shear=(-0.31,0.07)px" in line
    assert "R_shear=(0.29,-0.06)px" in line
    assert "L_aniso=1.13" in line
    assert "R_aniso=0.97" in line


def test_print_spells_out_a_missing_anisotropy(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(trace.ENV_VAR, str(tmp_path / "t.jsonl"))
    api = _client()
    readings = _readings()
    # No image in the payload and no server-side value: the derived reading
    # is None, which the log must say rather than render as a plausible 0.
    del readings["left_marker_anisotropy"], readings["right_marker_anisotropy"]
    with mock.patch("capx.integrations.tactile.univtac_api.requests.get",
                    return_value=_response(readings)):
        api.get_tactile_data()
    line = capsys.readouterr().out
    assert "L_aniso=n/a" in line and "R_aniso=n/a" in line
