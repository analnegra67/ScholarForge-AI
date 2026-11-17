"""Single source of truth for the package version (kept in sync with pyproject.toml by tests)."""

__version__ = "0.1.0"

# Version of the on-disk project format (scholarforge.yaml). Bumped only when the
# YAML layout changes incompatibly; database changes are handled by migrations.
PROJECT_FORMAT_VERSION = 1
