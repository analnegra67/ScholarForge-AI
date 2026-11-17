"""A ScholarForge project: one self-contained folder.

::

    <project>/
      scholarforge.yaml          configuration (human-editable)
      research/                  research plan, concept map (JSON + Markdown)
      content/                   block sources (Markdown, authoritative text)
      figures/<label>/           figure sources, renders, provenance
      assets/                    user-supplied images and files
      build/                     generated Quarto project and renders (disposable)
      exports/                   final deliverables (HTML, EPUB, PDF, print, LaTeX, Quarto)
      reports/                   verification / preflight reports
      .scholarforge/             database, history, checkpoints, logs
"""

from __future__ import annotations

from pathlib import Path

from scholarforge.models.project import ProjectConfig
from scholarforge.settings import Settings
from scholarforge.storage.checkpoints import CheckpointManager
from scholarforge.storage.content import ContentStore
from scholarforge.storage.migrate import open_database
from scholarforge.storage.repo import ProjectDB
from scholarforge.util.atomic import atomic_write_yaml, read_yaml

CONFIG_NAME = "scholarforge.yaml"
SUBDIRS = ("research", "content", "figures", "assets", "build", "exports", "reports", ".scholarforge")


class Project:
    def __init__(self, root: Path, config: ProjectConfig, settings: Settings):
        self.root = Path(root).resolve()
        self.config = config
        self.settings = settings
        self.db = ProjectDB(open_database(self.state_dir / "project.db"))
        self.content = ContentStore(self.root, self.db)
        self.checkpoints = CheckpointManager(self.root, self.db, keep_auto=settings.workflow.auto_checkpoint_keep)

    # -- locations ---------------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.config.id

    @property
    def state_dir(self) -> Path:
        return self.root / ".scholarforge"

    @property
    def research_dir(self) -> Path:
        return self.root / "research"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    # -- persistence ------------------------------------------------------------------------
    @staticmethod
    def load_config(root: Path) -> ProjectConfig:
        return ProjectConfig.model_validate(read_yaml(Path(root) / CONFIG_NAME))

    def save_config(self) -> None:
        atomic_write_yaml(self.root / CONFIG_NAME, self.config.model_dump(mode="json"))

    def reload_config(self) -> None:
        self.config = self.load_config(self.root)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.id} {self.config.title!r} at {self.root}>"
