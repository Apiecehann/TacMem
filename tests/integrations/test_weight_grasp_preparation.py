import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from capx.serving import launch_univtac_server as server


class Pose:
    def __init__(self, p, q):
        self.p = np.asarray(p, dtype=float)
        self.q = np.asarray(q, dtype=float)

    def to_transformation_matrix(self):
        matrix = np.eye(4)
        matrix[:3, :3] = Rotation.from_quat(self.q[[1, 2, 3, 0]]).as_matrix()
        matrix[:3, 3] = self.p
        return matrix

    def add_bias(self, delta):
        return Pose(self.p + self.to_transformation_matrix()[:3, :3] @ delta, self.q)


def construct_grasp_pose(position, grasp_from, camera_up):
    z_axis = -grasp_from / np.linalg.norm(grasp_from)
    x_axis = camera_up / np.linalg.norm(camera_up)
    matrix = np.column_stack([x_axis, np.cross(z_axis, x_axis), z_axis])
    quaternion = Rotation.from_matrix(matrix).as_quat()[[3, 0, 1, 2]]
    return Pose(position, quaternion)


@pytest.fixture
def scene(monkeypatch):
    transforms = ModuleType("envs.utils.transforms")
    transforms.Pose = Pose
    transforms.construct_grasp_pose = construct_grasp_pose
    atom = ModuleType("envs.utils.atom")
    atom.Action = lambda action, **kwargs: SimpleNamespace(action=action, **kwargs)
    monkeypatch.setitem(sys.modules, "envs.utils.transforms", transforms)
    monkeypatch.setitem(sys.modules, "envs.utils.atom", atom)
    data = {
        "found": True,
        "position": [0.38, 0.01, 0.002],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "bbox_extent": [0.015, 0.015, 0.08],
    }
    events = []
    robot = SimpleNamespace(
        gripper_center_to_ee=lambda pose: pose.add_bias([0.0, 0.0, -0.131]),
        get_ee_pose=Mock(return_value=Pose([0.5, -0.1, 0.3], [0, 1, 0, 0])),
    )
    task = SimpleNamespace(
        plan_success=True, _robot_manager=robot,
        pre_move=lambda: events.append("open"),
        move=Mock(side_effect=lambda *args, **kwargs: events.append("move") or True),
    )
    query = Mock(side_effect=lambda *args, **kwargs: dict(data))
    monkeypatch.setattr(server, "_TASK", task)
    monkeypatch.setattr(server, "_TASK_NAME", "weight_classify")
    monkeypatch.setattr(server, "_GRASP_OFFSET", {})
    monkeypatch.setattr(server, "_sim_get_object_pose", query)
    return SimpleNamespace(task=task, data=data, events=events, query=query)


def test_premove_leaves_open_hand_above_object_and_sample_returns_lower_target(scene):
    server._install_weight_pre_move(scene.task)
    scene.task.pre_move()

    assert scene.events == ["open", "move"]
    scene.query.assert_called_once_with("block", return_bbox_extent=True)
    args, kwargs = scene.task.move.call_args
    assert len(args[0]) == 1 and args[0][0].action == "move"
    assert kwargs["tag"] == "weight_pre_move"
    hover = args[0][0].target_pose
    hover_centre = hover.add_bias([0, 0, 0.131])
    np.testing.assert_allclose(hover_centre.p, [0.38, 0.01, 0.122])

    result = server._sim_get_grasp_pose("block", allow_ground_truth=True)
    grasp = Pose(result["position"], result["quaternion_wxyz"])
    np.testing.assert_allclose(grasp.add_bias([0, 0, 0.131]).p, [0.38, 0.01, 0.042])
    assert hover.p[2] - grasp.p[2] == pytest.approx(0.08)
    assert scene.events == ["open", "move"]
    np.testing.assert_allclose(
        server._sim_get_grasp_pose("block", allow_ground_truth=False)["position"],
        grasp.p,
    )


def test_sample_is_based_on_object_not_current_robot_pose_or_stale_cache(scene):
    server._GRASP_OFFSET["block"] = (np.ones(3), np.array([1, 0, 0, 0]))
    first = server._sim_get_grasp_pose("block", allow_ground_truth=True)
    scene.data["position"] = [0.42, -0.03, 0.012]
    second = server._sim_get_grasp_pose("block", allow_ground_truth=True)
    np.testing.assert_allclose(
        np.asarray(second["position"]) - first["position"], [0.04, -0.04, 0.01],
    )
    scene.task._robot_manager.get_ee_pose.assert_not_called()
    scene.task.move.assert_not_called()


def test_sample_follows_object_yaw(scene):
    scene.data["quaternion_wxyz"] = [np.sqrt(0.5), 0, 0, np.sqrt(0.5)]
    result = server._sim_get_grasp_pose("block", allow_ground_truth=True)
    rotation = Pose(result["position"], result["quaternion_wxyz"]).to_transformation_matrix()
    np.testing.assert_allclose(rotation[:3, 2], [0, 0, -1], atol=1e-12)
    np.testing.assert_allclose(rotation[:3, 0], [0, 1, 0], atol=1e-12)


def test_preparation_recomputes_for_next_reset(scene):
    server._install_weight_pre_move(scene.task)
    scene.task.pre_move()
    server._GRASP_OFFSET.clear()
    scene.data["position"] = [0.4, -0.01, 0.002]
    scene.task.pre_move()
    target = scene.task.move.call_args.args[0][0].target_pose
    np.testing.assert_allclose(target.p[:2], [0.4, -0.01])
    assert scene.events == ["open", "move", "open", "move"]


def test_failed_preparation_never_seeds_a_grasp_cache(scene):
    scene.task.move.side_effect = None
    scene.task.move.return_value = False
    server._install_weight_pre_move(scene.task)
    with pytest.raises(RuntimeError, match="move above block failed"):
        scene.task.pre_move()
    assert server._GRASP_OFFSET == {}


def test_failed_native_preparation_stops_before_queries(scene):
    scene.task.plan_success = False
    server._install_weight_pre_move(scene.task)
    with pytest.raises(RuntimeError, match="native pre_move failed"):
        scene.task.pre_move()
    scene.query.assert_not_called()
    scene.task.move.assert_not_called()


@pytest.mark.parametrize("extent", [None, [0.01, 0.02], [0.01, 0.02, float("nan")], [0.01, 0.02, 0]])
def test_missing_or_invalid_extent_does_not_move(scene, extent):
    scene.data["bbox_extent"] = extent
    server._install_weight_pre_move(scene.task)
    with pytest.raises(RuntimeError, match="extent unavailable"):
        scene.task.pre_move()
    scene.task.move.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("position", [0.3, float("nan"), 0.002]),
    ("quaternion_wxyz", [0, 0, 0, 0]),
])
def test_invalid_object_pose_rejected(scene, field, value):
    scene.data[field] = value
    with pytest.raises(ValueError, match="invalid block pose"):
        server._sim_get_grasp_pose("block", allow_ground_truth=True)
    scene.task.move.assert_not_called()


def test_unprivileged_query_requires_prepared_offset(scene):
    with pytest.raises(RuntimeError, match="no prepared grasp offset"):
        server._sim_get_grasp_pose("block", allow_ground_truth=False)


def test_other_tasks_keep_existing_grasp_contract(scene, monkeypatch):
    monkeypatch.setattr(server, "_TASK_NAME", "insert_hole")
    result = server._sim_get_grasp_pose("block", allow_ground_truth=True)
    np.testing.assert_allclose(result["position"], [0.5, -0.1, 0.3])
    scene.task.move.assert_not_called()
