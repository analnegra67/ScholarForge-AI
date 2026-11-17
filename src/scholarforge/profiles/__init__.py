"""Domain profiles: how a discipline is verified, sourced and presented.

Profiles are data (YAML), not code, and form an inheritance tree rooted at
``generic-science``. Users add their own in ``<config_dir>/profiles/*.yaml``; a
project can also carry overrides. Nothing in the engine is hard-wired to a list
of subjects.
"""

from scholarforge.profiles.registry import Profile, ProfileRegistry, get_registry

__all__ = ["Profile", "ProfileRegistry", "get_registry"]
