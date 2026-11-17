import json

from scholarforge.cli.main import main


def run(capsys, *argv):
    code = main(["--json", *argv])
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else None


def test_version(capsys):
    code, data = run(capsys, "version")
    assert code == 0 and data["version"]


def test_new_list_status_next_flow(capsys):
    code, created = run(capsys, "new", "--topic", "Differential Geometry", "--pages", "40", "--formats", "html,pdf")
    assert code == 0
    pid = created["project_id"]
    code, listed = run(capsys, "list")
    assert any(p["id"] == pid for p in listed["projects"])
    code, status = run(capsys, "status", pid)
    assert status["phase"] == "research"
    code, nxt = run(capsys, "next", pid, "--no-engine")
    assert nxt["status"] == "task" and nxt["task"]["kind"] == "research.plan"
    assert nxt["task"]["lease_token"]


def test_new_from_request(capsys):
    code, created = run(capsys, "new", "--request", "Create a handbook about Biochemistry of enzymes. Include figures.")
    assert code == 0 and created["title"].startswith("Biochemistry")


def test_errors_are_structured(capsys):
    code = main(["--json", "status", "prj_01ZZZZZZZZZZZZZZZZZZZZZZZZ"])
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out)["error"]["code"] == "PROJECT_NOT_FOUND"


def test_checkpoint_commands(capsys):
    _, created = run(capsys, "new", "--topic", "Number theory")
    pid = created["project_id"]
    code, cp = run(capsys, "checkpoint", "create", pid, "--reason", "manual test")
    assert code == 0 and cp["checkpoint"].startswith("cp_")
    _, listed = run(capsys, "checkpoint", "list", pid)
    assert any(c["id"] == cp["checkpoint"] for c in listed["checkpoints"])


def test_human_readable_output(capsys):
    _, created = run(capsys, "new", "--topic", "Category theory for programmers")
    pid = created["project_id"]
    assert main(["status", pid]) == 0
    out = capsys.readouterr().out
    assert "Category Theory" in out or "Category theory" in out
    assert "blocks" in out and "research" in out and "next:" in out
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "python" in out and ("ready" in out or "missing" in out)
    assert main(["list"]) == 0
    assert pid in capsys.readouterr().out


def test_doctor_runs(capsys):
    code, report = run(capsys, "doctor")
    assert code == 0
    names = {c["name"] for c in report["checks"]}
    assert {"python", "sqlite", "config", "data_dir"} <= names
