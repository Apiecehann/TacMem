"""Tactile skill library: prose skills, routed by task type and task name.

See `capx.tactile_skills.library` for the format and the routing rules.
Distinct from `capx.skills`, which extracts Python functions from trial code.
"""

from capx.tactile_skills.library import (
    FAMILIES,
    TactileSkill,
    TactileSkillLibrary,
    SkillError,
    default_library,
)

__all__ = [
    "FAMILIES",
    "SkillError",
    "TactileSkill",
    "TactileSkillLibrary",
    "default_library",
]
