import subprocess

import pytest

from termiai.tools import privilege
from termiai.tools.base import ToolError


def test_root_needs_nothing(ctx, monkeypatch):
    ctx.env.is_root = True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not call sudo as root"))
    privilege.ensure_admin(ctx)


def test_windows_explains_how_to_elevate(ctx):
    ctx.env.os = "windows"
    with pytest.raises(ToolError, match="Run as administrator"):
        privilege.ensure_admin(ctx)


def test_missing_sudo(ctx, monkeypatch):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: None)
    with pytest.raises(ToolError, match="not installed"):
        privilege.ensure_admin(ctx)


def test_cached_ticket_means_no_prompt(ctx, monkeypatch):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")
    monkeypatch.setattr(privilege, "sudo_ticket_valid", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not prompt"))
    privilege.ensure_admin(ctx)


def test_no_terminal_means_clear_error_not_a_hang(ctx, monkeypatch):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")
    monkeypatch.setattr(privilege, "sudo_ticket_valid", lambda: False)
    ctx.interactive = False
    with pytest.raises(ToolError, match="no interactive terminal"):
        privilege.ensure_admin(ctx)


def test_password_prompt_inherits_the_terminal_and_is_never_piped(ctx, monkeypatch, capsys):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")
    monkeypatch.setattr(privilege, "sudo_ticket_valid", lambda: False)
    ctx.interactive = True
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    privilege.ensure_admin(ctx)
    assert seen["cmd"] == ["sudo", "-v"]
    # No stdin/stdout/stderr/capture/input arguments at all => fully inherited terminal.
    assert not ({"stdin", "stdout", "stderr", "capture_output", "input"} & set(seen["kw"]))
    assert "never sees it" in capsys.readouterr().out


def test_failed_or_cancelled_authentication(ctx, monkeypatch):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")
    monkeypatch.setattr(privilege, "sudo_ticket_valid", lambda: False)
    ctx.interactive = True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1))
    with pytest.raises(ToolError, match="nothing was changed"):
        privilege.ensure_admin(ctx)


def test_sudo_ticket_valid_uses_noninteractive_check(monkeypatch):
    seen = {}
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert privilege.sudo_ticket_valid() is True
    assert seen["cmd"] == ["sudo", "-n", "true"] and seen["kw"]["stdin"] == subprocess.DEVNULL


def test_sudo_ticket_valid_false_on_error_or_missing(monkeypatch):
    monkeypatch.setattr(privilege.shutil, "which", lambda n: None)
    assert privilege.sudo_ticket_valid() is False
    monkeypatch.setattr(privilege.shutil, "which", lambda n: "/usr/bin/sudo")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("sudo", 15)

    monkeypatch.setattr(subprocess, "run", boom)
    assert privilege.sudo_ticket_valid() is False
