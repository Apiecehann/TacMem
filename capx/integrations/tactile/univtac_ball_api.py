"""Tactile API for the touch-only multi-material ball task.

Same transport, same tactile readings and the same motion primitives as
:class:`UniVTACTactileApi`; the difference is which tools this task can
actually honour.

Withdrawn (three):

* ``get_object_pose`` -- the balls are UIPC FEM bodies with no pose query, and
  the served line is touch-only by construction: the task's own policy
  observation carries no scene image, no object pose and no material label.
  Leaving the tool in would offer ground truth the task was built to deny.
* ``sample_grasp_pose`` -- it derives its pose from ``get_object_pose``.
* ``rotate_gripper`` -- there is no gripper-roll servo on this arm; the tool
  would answer "no _ik_to_center servo" on every call.

Added (one):

* ``get_ee_pose`` -- promoted from the parent's private diagnostic.  Without a
  scene camera or an object query there is no other way to learn the hand's
  current orientation, and ``goto_pose`` requires an absolute quaternion.
  It reports the arm's own kinematics (proprioception), not scene state, so it
  does not soften the touch-only premise.

Everything else -- including the accuracy caveat that the reported position is
the wrist flange -- is inherited unchanged, except for one scene fact that the
parent's text states for the other tactile scenes and this scene answers
differently: **the gripper centre here sits 0.118 m below the wrist flange
along the hand's local z, not 0.131 m.**

That length is measured, not assumed: ViTaForge's ball task re-derives its TCP
from the GelSight sensing-face vertices at every reset
(``env/tasks/blind_pick_balls.py``, ``tcp_offset``), and the server converts
every commanded end-effector pose to that TCP with it.  The 0.131 m the shared
docstring quotes belongs to the other tactile scenes' assembly; a grasp pose
built from it lands 13 mm above the pad centre it was meant to put on the
object.  ``goto_pose``'s docstring is therefore reissued below with this
scene's two lengths substituted for the shared ones, so the text the model
reads and the conversion the server performs describe the same robot.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from capx.integrations.tactile.univtac_api import UniVTACTactileApi

# Flange to the centre of the two sensing faces, along the hand's local z, in
# this scene's assembly.  Measured from the gel vertices (see the module
# docstring); the shared text's 0.131 m is another assembly's.
FLANGE_TO_PAD_CENTRE_M = 0.118
# The pads reach this far along the same axis.  The 2.5 mm past the centres is
# the shared text's own figure; only the starting point differs here.
FLANGE_TO_PAD_TIP_M = FLANGE_TO_PAD_CENTRE_M + 0.0025

# Tools this task cannot honour; see the module docstring.
_WITHDRAWN = ("get_object_pose", "sample_grasp_pose", "rotate_gripper")


class UniVTACBallApi(UniVTACTactileApi):
    """Robot control API for a Franka arm with GelSight Mini tactile sensors.

    Serves the touch-only ball retrieval task: motion, both fingertips'
    tactile readings, slip detection and proprioception.  Object perception is
    not available -- the balls have no pose query and the task is scored on
    touch alone.
    """

    def functions(self) -> dict[str, Any]:
        tools = super().functions()
        for name in _WITHDRAWN:
            tools.pop(name, None)
        tools = {"get_ee_pose": self.get_ee_pose, **tools}
        return tools

    def goto_pose(
        self,
        position: np.ndarray,
        quaternion_wxyz: np.ndarray,
        z_approach: float = 0.0,
    ) -> dict:
        # Same call, same server-side plan, same readings as the parent's --
        # only the docstring differs, because the parent quotes another
        # assembly's flange-to-pad length.  It is attached after the class
        # body so the inherited text stays the single source: the two lengths
        # are substituted, everything else is verbatim.
        return super().goto_pose(position, quaternion_wxyz, z_approach)

    def get_ee_pose(self) -> dict:
        """Return the end-effector pose in the world frame.

        The end effector is the wrist flange; the gripper centre sits about
        0.118 m further along the hand's local z axis.  This is the only source
        of the arm's current orientation, and ``goto_pose`` takes an absolute
        quaternion, so read it before commanding a pose whose rotation
        matters.

        Args:
            None.

        Returns:
            dict with ``position``: [x, y, z] metres and ``quaternion_wxyz``:
            [w, x, y, z], both in the world frame.
        """
        return self._get("/ee_pose")


def _with_scene_geometry(doc: str) -> str:
    """Quote this scene's flange-to-pad lengths in an inherited docstring.

    The parent states the two lengths in exactly two phrases, so substituting
    them keeps every other word -- the argument ranges, the return fields, the
    refusal caveat -- inherited rather than copied.  A test pins the
    substitution, so a reworded parent fails loudly instead of silently
    shipping the other assembly's number again.
    """
    return doc.replace(
        "0.131 m", f"{FLANGE_TO_PAD_CENTRE_M:.3f} m"
    ).replace(
        "about 0.134 m", f"about {FLANGE_TO_PAD_TIP_M:.3f} m"
    )


UniVTACBallApi.goto_pose.__doc__ = _with_scene_geometry(
    UniVTACTactileApi.goto_pose.__doc__
)
