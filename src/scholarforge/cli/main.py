"""``scholarforge`` command-line interface.

Every command returns a JSON-serialisable dict. ``--json`` prints it verbatim
(stable, machine-readable); otherwise a compact human rendering is printed.
Exit codes: 0 success, 1 handled error (with code and hint), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from scholarforge.errors import ScholarForgeError
from scholarforge.logging_setup import configure_logging
from scholarforge.version import __version__

Handler = Callable[[argparse.Namespace], dict[str, Any]]


# ----------------------------------------------------------------------------------- helpers
def _service():
    from scholarforge.core.service import ProjectService

    return ProjectService()


def _open(ref: str):
    return _service().open(ref)


def _coordinator(project):
    from scholarforge.core.workflow import Coordinator

    return Coordinator(project)


def _read_json_arg(value: str) -> Any:
    if value == "-":
        return json.loads(sys.stdin.read())
    p = Path(value)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(value)


# ----------------------------------------------------------------------------------- commands
def cmd_version(_: argparse.Namespace) -> dict:
    return {"version": __version__, "python": sys.version.split()[0]}


def cmd_doctor(args: argparse.Namespace) -> dict:
    from scholarforge.diagnostics import run_doctor

    return run_doctor(deep=args.deep)


def cmd_config(args: argparse.Namespace) -> dict:
    from scholarforge import paths
    from scholarforge.settings import load_settings, save_settings, set_setting

    settings = load_settings()
    if args.action == "show":
        return {"path": str(paths.config_file()), "settings": settings.to_yaml_dict()}
    if args.action == "path":
        return {"path": str(paths.config_file())}
    if args.action == "set":
        if not args.key or args.value is None:
            raise ScholarForgeError("usage: scholarforge config set <dotted.key> <value>")
        updated = set_setting(settings, args.key, args.value)
        save_settings(updated)
        return {"updated": args.key, "path": str(paths.config_file())}
    raise ScholarForgeError(f"unknown config action {args.action}")


def cmd_new(args: argparse.Namespace) -> dict:
    from scholarforge.core.intake import params_from_request
    from scholarforge.models.project import ProjectParams

    if args.params:
        params = ProjectParams.model_validate(_read_json_arg(args.params))
    elif args.request and not args.topic:
        params = params_from_request(args.request, language=args.language)
    else:
        if not args.topic:
            raise ScholarForgeError("provide --topic, --request or --params")
        data: dict[str, Any] = {"topic": args.topic}
        for key in ("title", "language", "level", "audience", "depth", "style", "request", "profile"):
            value = getattr(args, key, None)
            if value:
                data[key] = value
        if args.pages:
            data["target_pages"] = args.pages
        if args.formats:
            data["formats"] = [f.strip() for f in args.formats.split(",") if f.strip()]
        params = ProjectParams.model_validate(data)
    project = _service().create(params, location=Path(args.dir) if args.dir else None)
    try:
        return {"project_id": project.id, "path": str(project.root), "title": project.config.title,
                "next": "scholarforge next " + project.id}
    finally:
        project.close()


def cmd_list(_: argparse.Namespace) -> dict:
    return {"projects": _service().list()}


def cmd_status(args: argparse.Namespace) -> dict:
    with _open(args.project) as project:
        return _coordinator(project).status()


def cmd_next(args: argparse.Namespace) -> dict:
    with _open(args.project) as project:
        return _coordinator(project).next_task(run_engine=not args.no_engine)


def cmd_submit(args: argparse.Namespace) -> dict:
    with _open(args.project) as project:
        result = _read_json_arg(args.result)
        return _coordinator(project).submit_task(args.task_id, args.lease, result)


def cmd_resume(args: argparse.Namespace) -> dict:
    with _open(args.project) as project:
        return _coordinator(project).resume()


def cmd_checkpoint(args: argparse.Namespace) -> dict:
    with _open(args.project) as project:
        if args.action == "create":
            return {"checkpoint": project.checkpoints.create(args.reason or "manual", auto=False)}
        if args.action == "list":
            return {"checkpoints": project.checkpoints.list()}
        if args.action == "restore":
            if not args.checkpoint_id:
                raise ScholarForgeError("checkpoint id required")
            safety = project.checkpoints.restore(args.checkpoint_id)
            return {"restored": args.checkpoint_id, "safety_checkpoint": safety}
    raise ScholarForgeError(f"unknown checkpoint action {args.action}")


def cmd_verify(args: argparse.Namespace) -> dict:
    from scholarforge.science.pipeline import VerificationPipeline

    with _open(args.project) as project:
        return VerificationPipeline(project).run_all(network=not args.offline)


def cmd_report(args: argparse.Namespace) -> dict:
    from scholarforge.science.report import write_verification_report

    with _open(args.project) as project:
        return write_verification_report(project)


def cmd_build(args: argparse.Namespace) -> dict:
    from scholarforge.publishing.builder import BookBuilder

    formats = [f.strip() for f in args.formats.split(",")] if args.formats else None
    with _open(args.project) as project:
        return BookBuilder(project).build(formats=formats, clean=args.clean)


def cmd_preflight(args: argparse.Namespace) -> dict:
    from scholarforge.publishing.preflight import preflight_project

    with _open(args.project) as project:
        return preflight_project(project)


def cmd_export(args: argparse.Namespace) -> dict:
    from scholarforge.publishing.builder import BookBuilder

    with _open(args.project) as project:
        return BookBuilder(project).export_to(Path(args.dest))


def cmd_tools(args: argparse.Namespace) -> dict:
    from scholarforge.tools import installer
    from scholarforge.tools.detect import detect_all

    if args.action == "status":
        return {"tools": detect_all()}
    if args.action == "install":
        names = [n.strip() for n in (args.names or "quarto,tinytex,epubcheck").split(",") if n.strip()]
        return installer.install(names, assume_yes=args.yes)
    raise ScholarForgeError(f"unknown tools action {args.action}")


def cmd_mcp(_: argparse.Namespace) -> dict:
    from scholarforge.mcp.server import main as mcp_main

    mcp_main()
    return {}


def cmd_license(args: argparse.Namespace) -> dict:
    from scholarforge.licensing.client import LicenseClient

    client = LicenseClient()
    if args.action == "status":
        return client.status().model_dump(mode="json")
    if args.action == "activate":
        if not args.key:
            raise ScholarForgeError("license key required: scholarforge license activate <KEY>")
        return client.activate(args.key).model_dump(mode="json")
    if args.action == "refresh":
        return client.refresh().model_dump(mode="json")
    if args.action == "deactivate":
        return client.deactivate()
    raise ScholarForgeError(f"unknown license action {args.action}")


def cmd_secret(args: argparse.Namespace) -> dict:
    from scholarforge import secrets

    if args.action == "set":
        import getpass

        value = getpass.getpass(f"Value for {args.name} (input hidden): ")
        secrets.set_secret(args.name, value)
        return {"stored": args.name, "backend": "os-credential-store"}
    if args.action == "status":
        return {"name": args.name, "source": secrets.secret_source(args.name)}
    if args.action == "delete":
        return {"deleted": secrets.delete_secret(args.name)}
    raise ScholarForgeError(f"unknown secret action {args.action}")


def cmd_selftest(args: argparse.Namespace) -> dict:
    from scholarforge.e2e.scenario import run_scenario

    return run_scenario(workdir=Path(args.workdir) if args.workdir else None, render=not args.no_render,
                        network=args.network)


# ----------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scholarforge", description="ScholarForge scientific publishing engine")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version").set_defaults(func=cmd_version)

    d = sub.add_parser("doctor", help="diagnose the environment")
    d.add_argument("--deep", action="store_true", help="also run smoke tests (render a tiny document)")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("config", help="show or change settings")
    c.add_argument("action", choices=["show", "set", "path"])
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(func=cmd_config)

    n = sub.add_parser("new", help="create a project")
    n.add_argument("--topic")
    n.add_argument("--request", help="free-text request; parameters are inferred when --topic is absent")
    n.add_argument("--params", help="JSON object, file path or '-' with full ProjectParams")
    n.add_argument("--title")
    n.add_argument("--language")
    n.add_argument("--level")
    n.add_argument("--audience")
    n.add_argument("--depth", choices=["overview", "standard", "rigorous", "exhaustive"])
    n.add_argument("--style")
    n.add_argument("--profile")
    n.add_argument("--pages", type=int)
    n.add_argument("--formats", help="comma list: html,epub,pdf,print,latex,quarto")
    n.add_argument("--dir", help="explicit project directory")
    n.set_defaults(func=cmd_new)

    sub.add_parser("list", help="list known projects").set_defaults(func=cmd_list)

    for name, func, helptext in (
        ("status", cmd_status, "project status"),
        ("resume", cmd_resume, "resume after an interruption"),
        ("verify", cmd_verify, "run all scientific checks"),
        ("report", cmd_report, "write the verification report"),
        ("preflight", cmd_preflight, "preflight the latest build"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("project")
        if name == "verify":
            sp.add_argument("--offline", action="store_true")
        sp.set_defaults(func=func)

    nx = sub.add_parser("next", help="lease the next task for the host AI")
    nx.add_argument("project")
    nx.add_argument("--no-engine", action="store_true", help="do not run pending local jobs first")
    nx.set_defaults(func=cmd_next)

    sb = sub.add_parser("submit", help="submit a task result")
    sb.add_argument("project")
    sb.add_argument("task_id")
    sb.add_argument("--lease", required=True)
    sb.add_argument("--result", required=True, help="JSON, file path or '-'")
    sb.set_defaults(func=cmd_submit)

    cp = sub.add_parser("checkpoint", help="create/list/restore checkpoints")
    cp.add_argument("action", choices=["create", "list", "restore"])
    cp.add_argument("project")
    cp.add_argument("checkpoint_id", nargs="?")
    cp.add_argument("--reason")
    cp.set_defaults(func=cmd_checkpoint)

    b = sub.add_parser("build", help="build the book")
    b.add_argument("project")
    b.add_argument("--formats", help="comma list; default: project formats")
    b.add_argument("--clean", action="store_true")
    b.set_defaults(func=cmd_build)

    e = sub.add_parser("export", help="copy the latest deliverables to a folder")
    e.add_argument("project")
    e.add_argument("dest")
    e.set_defaults(func=cmd_export)

    t = sub.add_parser("tools", help="external tools (Quarto, TinyTeX, epubcheck)")
    t.add_argument("action", choices=["status", "install"])
    t.add_argument("names", nargs="?", help="comma list for install")
    t.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    t.set_defaults(func=cmd_tools)

    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=cmd_mcp)

    lic = sub.add_parser("license", help="subscription license")
    lic.add_argument("action", choices=["status", "activate", "refresh", "deactivate"])
    lic.add_argument("key", nargs="?")
    lic.set_defaults(func=cmd_license)

    sec = sub.add_parser("secret", help="store API keys in the OS credential store")
    sec.add_argument("action", choices=["set", "status", "delete"])
    sec.add_argument("name", choices=["openai_api_key", "openalex_api_key", "semantic_scholar_api_key"])
    sec.set_defaults(func=cmd_secret)

    st = sub.add_parser("selftest", help="run the end-to-end scenario (Deep Learning test book)")
    st.add_argument("--workdir")
    st.add_argument("--no-render", action="store_true")
    st.add_argument("--network", action="store_true", help="verify sources against live APIs")
    st.set_defaults(func=cmd_selftest)
    return p


def _print_human(command: str, result: Any) -> None:
    from scholarforge.cli.render import render

    text = render(command, result)
    try:
        print(text)
    except UnicodeEncodeError:  # legacy consoles
        print(text.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii"))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from scholarforge.settings import load_settings
    from scholarforge.util.winenv import complete_windows_env

    complete_windows_env()  # e.g. started by an MCP client or a scheduler with a minimal environment

    try:
        level = args.log_level or load_settings().log_level
    except ScholarForgeError:
        level = "INFO"
    configure_logging(level, stderr=args.command not in ("mcp",))
    try:
        result = args.func(args)
    except ScholarForgeError as exc:
        payload = {"error": exc.to_dict()}
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr if not args.json else sys.stdout)
        return 1
    except KeyboardInterrupt:
        print("interrupted; state is saved (resume with `scholarforge resume <project>`)", file=sys.stderr)
        return 130
    if args.command == "mcp":
        return 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        _print_human(args.command, result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
