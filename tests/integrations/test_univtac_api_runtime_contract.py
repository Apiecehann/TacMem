from types import ModuleType, SimpleNamespace
import sys

import pytest
import torch

from capx.integrations.tactile.univtac_api import UniVTACTactileApi, _trim_motion_tactile
from capx.serving import launch_univtac_server as server


@pytest.mark.parametrize("kwargs,expected", [
    ({}, True),
    ({"force_threshold": 20.5}, True),
    ({"position": 0.5}, False),
    ({"position": 0.5, "adaptive": True}, True),
    ({"force_threshold": 20.5, "adaptive": False}, False),
])
def test_close_default_matches_documented_control_mode(kwargs, expected):
    api = UniVTACTactileApi.__new__(UniVTACTactileApi)
    calls = []
    def post(path, data):
        calls.append((path, data))
        return {"tactile": {"left_depth_mm": 20.5, "right_depth_mm": 20.75, "step": 120}}
    api._post = post
    result = api.close_gripper(**kwargs)
    assert calls[0][1]["use_adaptive_grasp"] is expected
    assert result["left_depth_mm"] == 20.5
    assert result["step"] == 120


@pytest.mark.parametrize("task", ["weight_classify", "grasp_classify", "hardness_classify", "tactile_memory_match",
                                  "tension_strap", "bulb_tightening", "blind_pick_balls", "arm_shadow_piano"])
def test_runtime_selection_keeps_force_interfaces(task, monkeypatch, tmp_path):
    calibration = tmp_path / "UniVTAC" / "third_party" / "TacEx"
    native = tmp_path / "OpenTacBench"
    monkeypatch.delenv("CAPX_TACEX_RUNTIME_ROOT", raising=False)
    monkeypatch.setenv("UNIVTAC_TACEX_ROOT", str(calibration))
    monkeypatch.setattr(server, "_backing_for", lambda name: SimpleNamespace(root=native))
    staged = []
    monkeypatch.setattr(server, "_prefer_vitforge_tacex", staged.append)
    server._configure_task_tacex(task)
    expected = calibration if task in {"weight_classify", "grasp_classify", "hardness_classify", "tactile_memory_match"} else native / "third_party" / "TacEx"
    assert server.os.environ["CAPX_TACEX_RUNTIME_ROOT"] == str(expected)
    assert staged == [native]


@pytest.mark.parametrize("depths,expected", [([20.16, 20.75], True), ([20.5, 26.0], False),
                                          ([24.0, 24.0], False), ([18.0, 20.5], False)])
def test_adaptive_close_accepts_both_pads_in_target_band(depths, expected, monkeypatch):
    atom = ModuleType("envs.utils.atom")
    atom.Action = lambda **values: SimpleNamespace(**values)
    monkeypatch.setitem(sys.modules, "envs.utils.atom", atom)
    task = SimpleNamespace(
        cfg=SimpleNamespace(use_adaptive_grasp=False, adaptive_grasp_depth_threshold=27.5),
        _robot_manager=SimpleNamespace(get_gripper_qpos=lambda: 0.01),
        _tactile_manager=SimpleNamespace(get_min_depth=lambda: torch.tensor(depths)),
        move=lambda *args, **kwargs: True,
        plan_success=True, step_count=120,
    )
    monkeypatch.setattr(server, "_TASK", task)
    monkeypatch.setattr(server, "_TASK_NAME", "grasp_classify")
    if hasattr(server, "_OBSERVATION_MODE"):
        monkeypatch.setattr(server, "_OBSERVATION_MODE", "native")
    monkeypatch.setattr(server, "_record_frame", lambda *args: None)
    monkeypatch.setattr(server, "_sim_get_tactile_data", lambda: {})
    result = server._sim_gripper("close", 20.5, True)
    assert result["success"] is expected
    assert task.cfg.use_adaptive_grasp is False


def test_prompt_and_feedback_preserve_calibration_range():
    api = UniVTACTactileApi.__new__(UniVTACTactileApi)
    doc = api._doc_text(api.get_tactile_data)
    assert "below 24 mm" in doc
    assert "20.16-20.75" in doc
    assert "sensor's floor" not in doc
    assert _trim_motion_tactile({"left_depth_mm": 20.5, "step": 240}) == {"left_depth_mm": 20.5, "step": 240}
