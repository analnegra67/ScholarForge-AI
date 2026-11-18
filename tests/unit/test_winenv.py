"""Completion of the minimal environment that MCP clients pass to stdio servers on Windows."""

from scholarforge.util.winenv import DEFAULT_PATHEXT, complete_windows_env


def test_missing_variables_are_added():
    env = {"PATH": r"C:\Windows\System32", "SYSTEMROOT": r"C:\Windows", "TEMP": r"C:\Temp"}
    added = complete_windows_env(env, windows=True)
    assert set(added) == {"PATHEXT", "COMSPEC", "WINDIR", "TMP"}
    assert env["PATHEXT"] == DEFAULT_PATHEXT
    assert env["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert env["WINDIR"] == r"C:\Windows"
    assert env["TMP"] == r"C:\Temp"


def test_existing_values_are_kept_whatever_their_case():
    env = {"PathExt": ".EXE;.BAT", "ComSpec": r"D:\cmd.exe", "SystemRoot": r"D:\Win", "windir": r"D:\Win",
           "Temp": r"D:\T", "Tmp": r"D:\T2"}
    assert complete_windows_env(env, windows=True) == []
    assert env["PathExt"] == ".EXE;.BAT" and "PATHEXT" not in env


def test_nothing_happens_off_windows():
    env = {"PATH": "/usr/bin"}
    assert complete_windows_env(env, windows=False) == []
    assert env == {"PATH": "/usr/bin"}
