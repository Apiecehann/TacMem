"""Tests for the tactile reflex fast loop (tactile_reflex + server hook).

No GPU, no simulator.  Three layers are covered:

  1. Pure module (capx.serving.tactile_reflex): feature extraction from
     synthetic marker/depth tensors, the six event detectors, the stop
     decision and the digest summary.
  2. Server-adjacent (no sim): pydantic models — StopOn / MoveReflexive
     request+response, TactileResponse shear fields.
  3. Client mapping (univtac_api): move_reflexive / get_tactile_image wire
     shapes via stubbed _post/_get.
  4. Hook lifecycle (no sim): _install_reflex/_uninstall_reflex on a
     duck-typed fake task — save/restore of _step_callback, the set_arm
     freeze patch, error latching.

Importing launch_univtac_server runs its module-level setup (FastAPI app,
the end="\\r" print filter, the EpisodeRecorder instance); none of it
touches Isaac Sim, and no test here calls a simulation function.
"""

from __future__ import annotations

import base64
import io
import math
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
import torch
from PIL import Image
from pydantic import ValidationError

from capx.integrations.tactile.univtac_api import (
    UniVTACTactileApi,
    _MOTION_TACTILE_FIELDS,
    _marker_anisotropy,
    _trim_motion_tactile,
)
from capx.serving import launch_univtac_server as S
from capx.serving import tactile_reflex as tr

FAR_PLANE = tr.DEFAULT_THRESHOLDS["far_plane_mm"]


# ---------------------------------------------------------------------------
# extract_side_features — hand-computed checks on synthetic tensors
# ---------------------------------------------------------------------------

def _markers(m: int = 64, shift_x: float = 0.0, shift_y: float = 0.0,
             ref_seed: int = 0):
    """[2, M, 2] marker tensor; current = reference + (shift_x, shift_y)."""
    g = torch.Generator().manual_seed(ref_seed)
    ref = torch.rand(m, 2, generator=g) * 100.0
    cur = ref + torch.tensor([shift_x, shift_y])
    return torch.stack([ref, cur]), ref


def _depth(val: float, h: int = 24, w: int = 32) -> torch.Tensor:
    return torch.full((h, w), val)


def test_extract_uniform_shift_matches_hand_computed():
    marker, ref = _markers(shift_x=2.0, shift_y=1.5)
    feat = tr.extract_side_features(marker, ref, _depth(26.0), FAR_PLANE)

    assert feat.valid and feat.contact
    assert feat.depth_mm == 26.0
    assert feat.shear_dx == 2.0 and feat.shear_dy == 1.5
    assert feat.shear_mag == pytest.approx(math.hypot(2.0, 1.5))
    assert feat.coherence == pytest.approx(1.0, abs=1e-5)  # markers move together
    assert feat.mean_disp == pytest.approx(math.hypot(2.0, 1.5))  # per-marker mean
    # = server marker_displacement_px (threshold scale of detect_slip)


def test_extract_mixed_shift_keeps_coherent_part():
    marker, ref = _markers()
    cur = ref + torch.tensor([2.0, 1.0])
    cur[:32] = cur[:32] - torch.tensor([4.0, 0.0])  # half: (-2, 1), half: (2, 1)
    marker = torch.stack([ref, cur])

    feat = tr.extract_side_features(marker, ref, _depth(26.0), FAR_PLANE)

    assert feat.shear_dx == pytest.approx(0.0, abs=1e-5)   # x cancels (float32)
    assert feat.shear_dy == pytest.approx(1.0)
    # coherence = |mean_vec| / mean per-marker L2 = 1 / sqrt(5)
    assert feat.coherence == pytest.approx(1.0 / math.sqrt(5.0), abs=1e-5)
    assert feat.mean_disp == pytest.approx(math.hypot(2.0, 1.0))  # per-marker mean


def test_extract_reference_fallback_to_marker0():
    """reference=None must fall back to marker[0] (same as _sim_get_tactile_data)."""
    marker, _ = _markers(shift_x=1.0, shift_y=0.0)
    feat = tr.extract_side_features(marker, None, _depth(26.0), FAR_PLANE)
    assert feat.shear_dx == pytest.approx(1.0)   # delta = marker[1] - marker[0]


def test_extract_contact_boundary_and_missing_data():
    # contact = depth_min < far_plane - 0.1
    assert tr.extract_side_features(*_markers(), _depth(FAR_PLANE - 0.11),
                                    FAR_PLANE).contact is True
    assert tr.extract_side_features(*_markers(), _depth(FAR_PLANE - 0.1),
                                    FAR_PLANE).contact is False
    # missing marker or depth -> valid=False, no crash
    assert tr.extract_side_features(None, None, _depth(26.0), FAR_PLANE).valid is False
    assert tr.extract_side_features(*_markers(), None, FAR_PLANE).valid is False


# ---------------------------------------------------------------------------
# SideFeatures builder for detector tests (no raw tensors needed)
# ---------------------------------------------------------------------------

def _side(depth_mm: float = FAR_PLANE + 1.0, contact: bool | None = None,
          mean_disp: float = 0.0, shear_dx: float = 0.0, shear_dy: float = 0.0,
          shear_mag: float | None = None, coherence: float = 0.9,
          valid: bool = True) -> tr.SideFeatures:
    return tr.SideFeatures(
        valid=valid,
        contact=(depth_mm < FAR_PLANE - 0.1) if contact is None else contact,
        depth_mm=depth_mm,
        shear_dx=shear_dx, shear_dy=shear_dy,
        shear_mag=math.hypot(shear_dx, shear_dy) if shear_mag is None else shear_mag,
        coherence=coherence,
        mean_disp=mean_disp,
    )


def test_contact_event_fires_and_records_step():
    det = tr.TactileEventDetector()
    assert det.update(_side(depth_mm=FAR_PLANE - 0.11), None) == ["contact"]
    assert det.contact_step == 1
    assert det.latest_events == ["contact"]


# ---------------------------------------------------------------------------
# numeric stop_on overrides must actually override (regression: short event
# names used to be dropped by _merge_thresholds, so e.g. {"hard_depth": 28}
# silently kept the default 20.0 and never fired at depth 27.5)
# ---------------------------------------------------------------------------

def test_hard_depth_threshold_override_fires_above_default():
    det = tr.TactileEventDetector(stop_on={"hard_depth": 28.0})
    assert det.thresholds["hard_depth_mm"] == 28.0
    assert "hard_depth" in det.update(_side(depth_mm=27.5), None)  # 27.5 > 20

    # below the overridden threshold: no fire
    det2 = tr.TactileEventDetector(stop_on={"hard_depth": 28.0})
    assert "hard_depth" not in det2.update(_side(depth_mm=28.1), None)


def test_shear_spike_threshold_override():
    det = tr.TactileEventDetector(stop_on={"shear_spike": 5.0})
    assert det.thresholds["shear_spike_px"] == 5.0
    det.update(_side(mean_disp=0.5), None)
    assert det.update(_side(mean_disp=3.5), None) == []       # growth 3.0 < 5.0
    assert det.update(_side(mean_disp=10.0), None) == ["shear_spike"]  # growth 6.5


def test_slip_turn_threshold_override():
    det = tr.TactileEventDetector(stop_on={"slip": 90.0})
    assert det.thresholds["slip_turn_deg"] == 90.0
    det.update(_side(shear_dx=3.0, shear_dy=0.0), None)
    det.update(_side(shear_dx=2.12, shear_dy=2.12), None)   # 45° from step 1
    # 45° from step 2, still below the 90° override
    assert det.update(_side(shear_dx=0.0, shear_dy=3.0), None) == []
    # fresh detector: a full 90° turn fires
    det2 = tr.TactileEventDetector(stop_on={"slip": 90.0})
    det2.update(_side(shear_dx=3.0, shear_dy=0.0), None)
    assert det2.update(_side(shear_dx=0.0, shear_dy=3.0), None) == ["slip"]


def test_asymmetry_threshold_override():
    det = tr.TactileEventDetector(stop_on={"asymmetry": 5.0})
    assert det.thresholds["asymmetry_mm"] == 5.0
    assert "asymmetry" not in det.update(_side(depth_mm=30.0),
                                         _side(depth_mm=27.0))  # diff 3.0
    assert "asymmetry" in det.update(_side(depth_mm=30.0),
                                     _side(depth_mm=24.0))


def test_contact_numeric_override_redefines_far_plane():
    det = tr.TactileEventDetector(stop_on={"contact": 30.0})
    assert det.thresholds["far_plane_mm"] == 30.0
    assert det.far_plane_mm == 30.0
    # boolean enable leaves the far plane untouched
    det2 = tr.TactileEventDetector(stop_on={"contact": True})
    assert det2.far_plane_mm == FAR_PLANE


def test_plateau_dict_override_still_merges():
    det = tr.TactileEventDetector(stop_on={"plateau": {"mm": 1.5, "steps": 3}})
    assert det.thresholds["plateau_mm"] == 1.5
    assert det.thresholds["plateau_steps"] == 3.0
    assert det._window == 3  # max(3, plateau_steps)
    for _ in range(2):
        assert "plateau" not in det.update(_side(depth_mm=26.0), None)
    assert "plateau" in det.update(_side(depth_mm=26.0), None)  # 3rd flat step


def test_hard_depth_event_at_threshold():
    det = tr.TactileEventDetector()
    fired = det.update(_side(depth_mm=20.0), None)
    assert "hard_depth" in fired and "contact" in fired


def test_shear_spike_growth_with_coherence_gate():
    det = tr.TactileEventDetector()
    det.update(_side(mean_disp=0.5, coherence=0.9), None)
    fired = det.update(_side(mean_disp=3.0, coherence=0.9), None)  # growth 2.5
    assert "shear_spike" in fired

    det2 = tr.TactileEventDetector()
    det2.update(_side(mean_disp=0.5, coherence=0.4), None)
    fired2 = det2.update(_side(mean_disp=3.0, coherence=0.4), None)  # incoherent
    assert "shear_spike" not in fired2


def test_slip_direction_turn_needs_two_samples_and_magnitude():
    det = tr.TactileEventDetector()
    assert det.update(_side(shear_dx=3.0, shear_dy=0.0), None) == []  # 1st sample
    fired = det.update(_side(shear_dx=0.0, shear_dy=3.0), None)       # 90° turn
    assert "slip" in fired

    # noise gate: tiny vectors turning 90° are not slip
    det2 = tr.TactileEventDetector()
    det2.update(_side(shear_dx=0.3, shear_dy=0.0), None)
    fired2 = det2.update(_side(shear_dx=0.0, shear_dy=0.3), None)
    assert "slip" not in fired2


def test_plateau_requires_full_flat_pressing_window():
    det = tr.TactileEventDetector()
    for _ in range(4):
        assert "plateau" not in det.update(_side(depth_mm=26.0), None)
    fired = det.update(_side(depth_mm=26.0), None)  # 5th flat pressing step
    assert "plateau" in fired

    # hovering in contact (depth > light grasp 27.5) never plateaus
    det2 = tr.TactileEventDetector()
    for _ in range(6):
        assert "plateau" not in det2.update(_side(depth_mm=33.0), None)


def test_asymmetry_needs_contact_and_both_sides():
    det = tr.TactileEventDetector()
    fired = det.update(_side(depth_mm=30.0), _side(depth_mm=27.0))  # diff 3.0
    assert "asymmetry" in fired

    det2 = tr.TactileEventDetector()
    # both sides at/beyond far plane (no contact): far-plane noise must not fire
    assert det2.update(_side(depth_mm=FAR_PLANE + 1.0), _side(depth_mm=FAR_PLANE + 0.5),
                       ) == []


def test_evaluate_severity_order_and_enabling():
    det = tr.TactileEventDetector(stop_on={"hard_depth": 20, "contact": True})
    det.update(_side(depth_mm=20.0), None)  # fires contact + hard_depth
    assert tr.evaluate({"hard_depth": 20, "contact": True}, det) == (True, "hard_depth")

    # an event that fired but is not enabled does not stop the motion
    det2 = tr.TactileEventDetector(stop_on={"contact": True})
    det2.update(_side(depth_mm=20.0), None)
    assert tr.evaluate({"contact": True}, det2) == (True, "contact")

    # empty dict = conditions disabled
    det3 = tr.TactileEventDetector()
    det3.update(_side(depth_mm=20.0), None)
    assert tr.evaluate({}, det3) == (False, None)


def test_evaluate_monitoring_mode_never_triggers():
    det = tr.TactileEventDetector()
    det.update(_side(depth_mm=15.0), _side(depth_mm=15.0))
    assert tr.evaluate(None, det) == (False, None)


def test_evaluate_max_steps_safety_net():
    det = tr.TactileEventDetector()
    stop_on = {"max_steps": 3}
    det.update(_side(depth_mm=FAR_PLANE + 1.0), None)  # no events
    assert tr.evaluate(stop_on, det) == (False, None)
    det.update(_side(depth_mm=FAR_PLANE + 1.0), None)
    assert tr.evaluate(stop_on, det) == (False, None)
    det.update(_side(depth_mm=FAR_PLANE + 1.0), None)  # n_updates == 3
    assert tr.evaluate(stop_on, det) == (True, "max_steps")


def test_summary_digest_fields():
    det = tr.TactileEventDetector()
    det.update(_side(depth_mm=30.0, shear_dx=1.0), _side(depth_mm=33.0, shear_dx=-1.0))
    det.update(_side(depth_mm=26.0, shear_dx=2.0, shear_dy=1.0, mean_disp=3.0),
               _side(depth_mm=30.0))
    det.update(_side(depth_mm=26.0, shear_dx=2.0, shear_dy=1.0),
               _side(depth_mm=26.0))

    s = det.summary
    assert s["max_shear_px"] == pytest.approx(math.hypot(2.0, 1.0), abs=0.001)
    assert s["min_depth_mm"] == pytest.approx(26.0)
    assert s["asymmetry_mm"] == pytest.approx(4.0)  # max |L-R| over steps
    assert s["contact_step"] == 1
    assert s["events"][0] == "contact"              # first-fire, deduped
    assert "asymmetry" in s["events"]               # fired at step 1 (diff 3)
    assert s["shear_dir_deg"] == pytest.approx(math.degrees(math.atan2(1.0, 2.0)),
                                               abs=0.1)  # dominant side at last step


# ---------------------------------------------------------------------------
# Server-adjacent: pydantic models
# ---------------------------------------------------------------------------

def test_stop_on_defaults_and_validation():
    s = S.StopOn()
    assert s.contact is False and s.hard_depth is None and s.plateau is None

    s2 = S.StopOn(contact=True, hard_depth=15.5, plateau={"mm": 0.3, "steps": 4})
    assert s2.model_dump() == {
        "contact": True, "hard_depth": 15.5,
        "shear_spike": None, "slip": None, "asymmetry": None,
        "plateau": {"mm": 0.3, "steps": 4}, "max_steps": None,
    }
    with pytest.raises(ValidationError):
        S.StopOn(hard_depth="deep")  # type: ignore[arg-type]


def test_move_reflexive_request_defaults_and_stop_on():
    r = S.MoveReflexiveRequest(dz=-0.005)
    assert r.dz == -0.005 and r.tag == "delta_reflexive" and r.stop_on is None
    r2 = S.MoveReflexiveRequest(dz=-0.005, stop_on={"contact": True})
    assert r2.stop_on.contact is True  # type: ignore[union-attr]


def test_move_reflexive_response_roundtrip_event():
    resp = S.MoveReflexiveResponse(
        success=True, terminated_by="contact",
        event={"terminated_by": "contact", "steps": 3, "events": ["contact"]},
    )
    d = resp.model_dump()
    assert d["terminated_by"] == "contact"
    assert d["event"]["steps"] == 3


def test_tactile_response_accepts_shear_fields():
    t = S.TactileResponse(left_marker_shear_dx=-1.5, right_marker_shear_dy=2.25)
    d = t.model_dump()
    assert d["left_marker_shear_dx"] == -1.5
    assert d["right_marker_shear_dy"] == 2.25
    assert d["left_marker_shear_dy"] == 0.0  # defaults


# ---------------------------------------------------------------------------
# Client mapping (stubbed _post / _get)
# ---------------------------------------------------------------------------

def _make_client() -> UniVTACTactileApi:
    return UniVTACTactileApi(object(), server_url="http://test:8120")


def test_move_reflexive_posts_body_and_returns_scalars_not_images():
    api = _make_client()
    scalars = {k: 0.0 for k in _MOTION_TACTILE_FIELDS}
    server_resp = {
        "success": True,
        "tactile": {
            **scalars,
            "left_marker_anisotropy": 1.25,
            "left_rgb_base64": "x",
            "left_depth_base64": "y",
        },
        "terminated_by": "contact",
        "event": {"terminated_by": "contact", "steps": 2, "events": ["contact"]},
    }
    api._post = mock.Mock(return_value=server_resp)  # type: ignore[method-assign]

    res = api.move_reflexive(dz=-0.005, stop_on={"contact": True})

    # Every scalar survives, the images do not, and an anisotropy the server
    # sent is passed through rather than recomputed.
    assert set(res["tactile"]) == set(_MOTION_TACTILE_FIELDS) | {
        "left_marker_anisotropy"
    }
    assert res["tactile"]["left_marker_anisotropy"] == 1.25
    assert not any(k.endswith("_base64") for k in res["tactile"])
    assert res["terminated_by"] == "contact"
    assert res["event"]["steps"] == 2
    path, body = api._post.call_args[0]  # type: ignore[union-attr]
    assert path == "/move_reflexive"
    assert body["dz"] == -0.005
    assert body["stop_on"] == {"contact": True}
    assert body["tag"] == "move_reflexive"


def _marker_png_b64(array: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(array.astype(np.uint8)).save(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def test_motion_tactile_derives_anisotropy_from_the_marker_image():
    # g[i, j] = 16*i + j: row-to-row gradient 16, column-to-column 1.
    banded = np.add.outer(np.arange(16) * 16, np.arange(16))
    out = _trim_motion_tactile({"left_rgb_base64": _marker_png_b64(banded)})
    assert out["left_marker_anisotropy"] == pytest.approx(16.0, rel=0.05)

    # g[i, j] = i + j: both gradients 1, an evenly loaded gel.
    even = np.add.outer(np.arange(16), np.arange(16))
    out = _trim_motion_tactile({"right_rgb_base64": _marker_png_b64(even)})
    assert out["right_marker_anisotropy"] == pytest.approx(1.0, rel=0.05)


def test_server_and_client_anisotropy_agree_on_the_same_image():
    """The reading is computed server-side now; the number must not move."""
    arr = np.random.default_rng(0).integers(0, 256, size=(24, 32, 3), dtype=np.uint8)
    tensor = torch.from_numpy(arr)

    server = S._rgb_tensor_anisotropy(tensor)
    client = _marker_anisotropy(S._rgb_tensor_to_base64(tensor))

    assert server is not None and client is not None
    assert server == pytest.approx(client, rel=1e-12)
    assert S._rgb_tensor_anisotropy(None) is None


def test_tactile_response_carries_anisotropy_and_drops_std():
    d = S.TactileResponse().model_dump()
    assert d["left_marker_anisotropy"] is None  # None, never a fake 1.0
    assert d["right_marker_anisotropy"] is None
    assert not any("std_px" in k for k in d)


def _banded_marker_b64() -> str:
    banded = np.add.outer(np.arange(16) * 16, np.arange(16)).astype(np.uint8)
    return S._rgb_tensor_to_base64(torch.from_numpy(banded))


def test_get_tactile_data_prefers_the_server_anisotropy():
    api = _make_client()
    payload = S.TactileResponse().model_dump()
    payload["left_marker_anisotropy"] = 7.5
    payload["left_rgb_base64"] = _banded_marker_b64()  # would derive 16.0
    api._get = mock.Mock(return_value=payload)  # type: ignore[method-assign]

    d = api.get_tactile_data()

    assert d["left_marker_anisotropy"] == 7.5  # not recomputed from the image


def test_get_tactile_data_derives_anisotropy_when_the_server_omits_it():
    api = _make_client()
    payload = S.TactileResponse().model_dump()  # anisotropy defaults to None
    payload["right_rgb_base64"] = _banded_marker_b64()
    api._get = mock.Mock(return_value=payload)  # type: ignore[method-assign]

    d = api.get_tactile_data()

    assert d["right_marker_anisotropy"] == pytest.approx(16.0, rel=0.05)


def test_motion_tactile_survives_an_undecodable_marker_image():
    out = _trim_motion_tactile(
        {
            "left_rgb_base64": base64.b64encode(b"not an image").decode(),
            "left_contact": True,
        }
    )
    assert "left_marker_anisotropy" not in out
    assert out["left_contact"] is True  # the rest of the reading is untouched


def test_move_reflexive_stop_on_none_passes_through():
    api = _make_client()
    api._post = mock.Mock(return_value={  # type: ignore[method-assign]
        "success": True, "tactile": {}, "terminated_by": None, "event": {},
    })
    res = api.move_reflexive(dz=-0.005)
    assert res["terminated_by"] is None and res["event"] == {}
    assert api._post.call_args[0][1]["stop_on"] is None  # type: ignore[union-attr]


def test_move_reflexive_raises_on_falsy_success():
    """The real _post contract: falsy success raises RuntimeError."""
    class _FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"success": False, "message": "Motion planning failed"}

    api = _make_client()
    with mock.patch("capx.integrations.tactile.univtac_api.requests.post",
                    return_value=_FakeResp()):
        with pytest.raises(RuntimeError, match="Motion failed"):
            api.move_reflexive(dz=-0.005)


def test_get_tactile_image_decodes_png():
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:, :, 1] = 7
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    api = _make_client()
    api._get = mock.Mock(return_value={"left_rgb_base64": b64})  # type: ignore[method-assign]
    out = api.get_tactile_image("left")
    assert out is not None and out.shape == (240, 320, 3) and out.dtype == np.uint8
    assert out[0, 0, 1] == 7

    api._get.return_value = {"left_rgb_base64": ""}  # type: ignore[union-attr]
    assert api.get_tactile_image("left") is None
    api._get.return_value = {"right_rgb_base64": b64}  # wrong side key
    assert api.get_tactile_image("left") is None


# ---------------------------------------------------------------------------
# Hook lifecycle on a duck-typed fake task (no sim)
# ---------------------------------------------------------------------------

class _FakeRobotManager:
    def __init__(self):
        self.calls = 0

    def set_arm(self, *args, **kwargs):
        self.calls += 1


class _FakeTask:
    _step_callback = staticmethod(lambda status: "orig")  # mirrors _base_task

    def __init__(self):
        self.plan_success = True
        self.step_count = 10
        self.cfg = SimpleNamespace(robot=SimpleNamespace(tactile_far_plane=FAR_PLANE))
        self._robot_manager = _FakeRobotManager()
        self.render_calls = 0
        self.obs = _fake_obs()

    def _update_render(self):
        self.render_calls += 1

    def _get_observations(self):
        return self.obs


def _fake_obs(depth_val: float = 26.0) -> dict:
    marker = torch.zeros(2, 64, 2)
    depth = torch.full((24, 32), depth_val)
    return {"tactile": {
        "left_tactile": {"marker": marker.clone(), "depth": depth.clone()},
        "right_tactile": {"marker": marker.clone(), "depth": depth.clone()},
    }}


@pytest.fixture
def fake_task(monkeypatch):
    fake = _FakeTask()
    monkeypatch.setattr(S, "_TASK", fake)
    return fake


def test_install_uninstall_restores_callback(fake_task):
    det = tr.TactileEventDetector()
    ctx = S.ReflexContext()
    S._install_reflex(det, None, ctx)
    assert "_step_callback" in fake_task.__dict__
    S._uninstall_reflex(ctx)
    assert "_step_callback" not in fake_task.__dict__
    assert fake_task._step_callback({"mode": "collect"}) == "orig"  # class binding back

    # a pre-existing instance attr is restored verbatim
    orig = lambda status: "custom"
    fake_task._step_callback = orig
    ctx2 = S.ReflexContext()
    S._install_reflex(det, None, ctx2)
    S._uninstall_reflex(ctx2)
    assert fake_task.__dict__["_step_callback"] is orig


def test_callback_trigger_freezes_arm_and_no_retrigger(fake_task):
    det = tr.TactileEventDetector(stop_on={"contact": True})
    ctx = S.ReflexContext()
    S._install_reflex(det, {"contact": True}, ctx)
    cb = fake_task.__dict__["_step_callback"]

    assert cb({}) == "reflex"
    assert ctx.triggered and ctx.reason == "contact"
    assert ctx.step == 10
    assert fake_task.plan_success is False                     # physics frozen
    assert fake_task._robot_manager.__dict__["set_arm"] is S._noop_arm  # no teleports

    cb({})  # second call: contact still fires, but no re-trigger
    assert ctx.step == 10 and ctx.triggered
    assert fake_task.render_calls == 2

    S._uninstall_reflex(ctx)
    assert "set_arm" not in fake_task._robot_manager.__dict__  # binding restored
    fake_task._robot_manager.set_arm(1)
    assert fake_task._robot_manager.calls == 1


def test_callback_error_latched_never_propagates(fake_task):
    det = tr.TactileEventDetector(stop_on={"contact": True})
    ctx = S.ReflexContext()
    S._install_reflex(det, {"contact": True}, ctx)
    cb = fake_task.__dict__["_step_callback"]

    def boom():
        raise RuntimeError("sensor dead")

    fake_task._get_observations = boom
    assert cb({}) == "reflex"          # error caught inside the callback
    assert "sensor dead" in ctx.error
    assert not ctx.triggered and fake_task.plan_success is True

    assert cb({}) == "reflex"          # latched: no re-render, no raise
    assert fake_task.render_calls == 1
    S._uninstall_reflex(ctx)
