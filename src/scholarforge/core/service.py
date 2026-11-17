"""Project lifecycle: create, open, list, forget."""

from __future__ import annotations

from pathlib import Path

from scholarforge.errors import InvalidInput, ProjectExists, ProjectNotFound
from scholarforge.logging_setup import get_logger
from scholarforge.models.project import BuildSettings, PrintSettings, ProjectConfig, ProjectParams
from scholarforge.paths import is_cloud_synced
from scholarforge.settings import Settings, load_settings
from scholarforge.core.project import CONFIG_NAME, SUBDIRS, Project
from scholarforge.core.registry import ProjectRegistry
from scholarforge.util.ids import is_valid_id, new_id
from scholarforge.util.text import slugify
from scholarforge.util.timeutil import now_iso
from scholarforge.version import __version__

log = get_logger("service")


class ProjectService:
    def __init__(self, settings: Settings | None = None, registry: ProjectRegistry | None = None):
        self.settings = settings or load_settings()
        self.registry = registry or ProjectRegistry()

    # -- create ------------------------------------------------------------------------------
    def create(
        self,
        params: ProjectParams,
        *,
        location: Path | None = None,
        print_settings: PrintSettings | None = None,
        build_settings: BuildSettings | None = None,
        initialize_workflow: bool = True,
    ) -> Project:
        title = params.title or params.topic
        slug = slugify(title, max_len=48, fallback="book")
        if location is not None:
            root = Path(location).expanduser().resolve()
            if (root / CONFIG_NAME).exists():
                raise ProjectExists(f"a project already exists at {root}")
            if root.exists() and any(root.iterdir()):
                raise InvalidInput(f"target directory is not empty: {root}")
        else:
            base = Path(self.settings.projects_root).expanduser().resolve()
            root = base / slug
            n = 2
            while root.exists():
                root = base / f"{slug}-{n}"
                n += 1
        for sub in SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)

        config = ProjectConfig(
            id=new_id("prj"),
            slug=root.name,
            created_at=now_iso(),
            engine_version=__version__,
            params=params,
            print=print_settings or PrintSettings(),
            build=build_settings or BuildSettings(),
        )
        project = Project(root, config, self.settings)
        project.save_config()
        project.db.meta.set("project_id", config.id)
        project.db.meta.set("created_at", config.created_at)
        project.db.events.log("project.created", "project", config.id, {"title": title, "engine": __version__})
        self.registry.add(config.id, root, title)
        if is_cloud_synced(root):
            log.warning("project %s is inside a cloud-synced folder; keep it local if you see locking errors", config.id)
        if initialize_workflow:
            from scholarforge.core.workflow import Coordinator

            Coordinator(project).initialize()
        log.info("created project %s at %s", config.id, root)
        return project

    # -- open ----------------------------------------------------------------------------------
    def resolve_root(self, ref: str | Path) -> Path:
        ref_str = str(ref)
        candidate = Path(ref_str).expanduser()
        if (candidate / CONFIG_NAME).exists():
            return candidate.resolve()
        if is_valid_id(ref_str, "prj"):
            path = self.registry.get(ref_str)
            if path and (path / CONFIG_NAME).exists():
                return path
            # registry stale: rescan the default projects root
            self.registry.scan(Path(self.settings.projects_root))
            path = self.registry.get(ref_str)
            if path and (path / CONFIG_NAME).exists():
                return path
        # slug or folder name under the projects root
        by_slug = Path(self.settings.projects_root).expanduser() / ref_str
        if (by_slug / CONFIG_NAME).exists():
            return by_slug.resolve()
        for pid, entry in self.registry.entries().items():
            if Path(entry["path"]).name == ref_str and (Path(entry["path"]) / CONFIG_NAME).exists():
                return Path(entry["path"])
        raise ProjectNotFound(
            f"project not found: {ref_str}",
            hint="use `scholarforge list` (or the list_projects tool) to see known projects",
        )

    def open(self, ref: str | Path) -> Project:
        root = self.resolve_root(ref)
        config = Project.load_config(root)
        project = Project(root, config, self.settings)
        stored = project.db.meta.get("project_id")
        if stored and stored != config.id:
            project.close()
            raise InvalidInput(f"project id mismatch between {CONFIG_NAME} and database in {root}")
        if self.registry.get(config.id) is None:
            self.registry.add(config.id, root, config.title)
        self.registry.touch(config.id)
        return project

    def list(self) -> list[dict]:
        self.registry.scan(Path(self.settings.projects_root))
        out = []
        for pid, entry in sorted(self.registry.entries().items(), key=lambda kv: kv[1].get("registered_at", "")):
            root = Path(entry["path"])
            exists = (root / CONFIG_NAME).exists()
            out.append({"id": pid, "title": entry.get("title"), "path": str(root), "exists": exists,
                        "last_opened": entry.get("last_opened")})
        return out

    def forget(self, ref: str) -> bool:
        """Remove from the registry only; never deletes the user's files."""
        if is_valid_id(ref, "prj"):
            return self.registry.remove(ref)
        root = self.resolve_root(ref)
        cfg = Project.load_config(root)
        return self.registry.remove(cfg.id)
