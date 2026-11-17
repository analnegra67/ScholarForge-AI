"""MCP server tests: in-process client (modern negotiation) and a real stdio subprocess (legacy handshake)."""

import json
import os
import sys

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from scholarforge.mcp.server import build_server

EXPECTED_TOOLS = {
    "diagnostics", "install_tools", "create_project", "list_projects", "project_status", "resume_project",
    "next_task", "submit_task", "get_outline", "read_block", "save_block", "add_source", "list_sources",
    "run_checks", "check_math", "verification_report", "terms", "render_figure", "get_figure_preview",
    "register_image", "confirm_external_image", "build_book", "preflight_report", "configure_project",
    "checkpoints", "license_status", "activate_license",
}


def _payload(result):
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return json.loads(text)


async def _session_flow():
    server = build_server()
    async with Client(server) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert EXPECTED_TOOLS <= names, EXPECTED_TOOLS - names
        for t in tools.tools:
            assert t.description and t.input_schema["type"] == "object"
        created = _payload(await client.call_tool("create_project", {"topic": "Topology for physicists",
                                                                      "target_pages": 20}))
        pid = created["project_id"]
        nxt = _payload(await client.call_tool("next_task", {"project_id": pid}))
        assert nxt["status"] == "task" and nxt["task"]["kind"] == "research.plan"
        bad = await client.call_tool("submit_task", {"project_id": pid, "task_id": nxt["task"]["task_id"],
                                                     "lease_token": nxt["task"]["lease_token"], "result": {}})
        assert _payload(bad)["accepted"] is False
        err = await client.call_tool("project_status", {"project_id": "prj_01ZZZZZZZZZZZZZZZZZZZZZZZZ"})
        assert err.is_error and "PROJECT_NOT_FOUND" in err.content[0].text
        math = _payload(await client.call_tool("check_math", {"checks": [
            {"id": "a", "kind": "identity", "lhs": "sin(x)**2 + cos(x)**2", "rhs": "1"}]}))
        assert math["results"][0]["outcome"] == "pass" and math["results"][0]["proof"] is True
        st = _payload(await client.call_tool("project_status", {"project_id": pid}))
        assert st["phase"] == "research"
        cps = _payload(await client.call_tool("checkpoints", {"project_id": pid, "action": "restore",
                                                              "checkpoint_id": "cp_x"}))
        assert cps["confirmation_required"] is True
        lic = _payload(await client.call_tool("license_status", {}))
        assert lic["enforcement"] == "off"


def test_in_process_session():
    anyio.run(_session_flow)


async def _stdio_flow(env):
    params = StdioServerParameters(command=sys.executable, args=["-m", "scholarforge.mcp.server"], env=env)
    with anyio.fail_after(120):
        async with Client(params, mode="legacy") as client:
            tools = await client.list_tools()
            assert "next_task" in {t.name for t in tools.tools}
            res = _payload(await client.call_tool("list_projects", {}))
            assert res == {"projects": []}


def test_stdio_subprocess_legacy_handshake(sf_home):
    """A real child process speaking MCP over stdio, with the classic initialize handshake used by clients."""
    env = {k: v for k, v in os.environ.items()}
    env["SCHOLARFORGE_HOME"] = str(sf_home)
    anyio.run(_stdio_flow, env)
