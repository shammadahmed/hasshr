import pytest

from termiai.contracts import Reversibility as R
from termiai.tools import system as sysmod
from termiai.tools.procutil import Captured, run_capture, try_capture
from termiai.tools.system import (
    GetSystemInfo,
    ReadDeviceInfo,
    ReadLogs,
    ReadPackageState,
    ReadServiceState,
)


def fake_capture(mapping):
    """Build a replacement for run_capture/try_capture keyed by argv prefix."""
    calls = []

    def fn(argv, timeout=30):
        calls.append(argv)
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return Captured(1, "", "unmapped")

    fn.calls = calls
    return fn


def patch_capture(monkeypatch, mapping, have_all=True):
    fn = fake_capture(mapping)
    monkeypatch.setattr(sysmod, "run_capture", fn)
    monkeypatch.setattr(sysmod, "try_capture", fn)
    monkeypatch.setattr(sysmod, "have", lambda p: have_all)
    return fn


def test_every_diagnostic_tool_is_read_only_and_full(ctx):
    for t in (GetSystemInfo(), ReadLogs(), ReadDeviceInfo(), ReadPackageState(), ReadServiceState()):
        assert t.read_only and t.reversibility == R.FULL, t.name


# ---------------------------------------------------------------- procutil
def test_run_capture_missing_program_and_timeout():
    from termiai.tools.base import ToolError

    with pytest.raises(ToolError, match="not installed"):
        run_capture(["definitely_not_a_program_xyz"])
    assert try_capture(["definitely_not_a_program_xyz"]) is None
    with pytest.raises(ToolError, match="took longer"):
        run_capture(["sleep", "5"], timeout=1)
    cap = run_capture(["sh", "-c", "echo hi; exit 2"])
    assert cap.returncode == 2 and cap.stdout.strip() == "hi" and not cap.ok


# ---------------------------------------------------------------- system info
def test_get_system_info(ctx):
    r = GetSystemInfo().run({}, ctx)
    assert r.ok and "Ubuntu" in r.output and "6.8.0-45-generic" in r.output
    assert r.data["package_manager"] == "apt" and r.data["untrusted"] is False and r.data["cpu_count"]


# ---------------------------------------------------------------- logs
def test_read_logs_builds_safe_argv_and_tails(ctx, monkeypatch):
    out = "\n".join(f"line{i}" for i in range(10))
    fn = patch_capture(monkeypatch, {("journalctl",): Captured(0, out, "")})
    r = ReadLogs().run({"unit": "NetworkManager.service", "priority": "err", "since": "1 hour ago", "lines": 3}, ctx)
    assert r.ok and r.output.splitlines() == ["line7", "line8", "line9"] and r.data["untrusted"] is True
    argv = fn.calls[-1]
    assert argv[0] == "journalctl" and "-u" in argv and "NetworkManager.service" in argv and "err" in argv
    assert "--since" in argv and "1 hour ago" in argv and "-b" in argv and "0" in argv
    assert "sudo" not in argv and "sh" not in argv


def test_read_logs_previous_boot_and_contains_filter(ctx, monkeypatch):
    fn = patch_capture(monkeypatch, {("journalctl",): Captured(0, "ok\nERROR snd_hda failed\nok2", "")})
    r = ReadLogs().run({"boot": "previous", "contains": "snd_hda"}, ctx)
    assert r.output == "ERROR snd_hda failed" and "-1" in fn.calls[-1]


@pytest.mark.parametrize("bad", [{"unit": "x; rm -rf /"}, {"unit": "a b"}, {"unit": "$(id)"}, {"since": "now; reboot"}, {"since": "`id`"}])
def test_read_logs_rejects_injection_in_arguments(ctx, monkeypatch, bad):
    fn = patch_capture(monkeypatch, {("journalctl",): Captured(0, "x", "")})
    r = ReadLogs().run(bad, ctx)
    assert not r.ok and fn.calls == []


def test_read_logs_kernel_falls_back_to_journal_when_dmesg_restricted(ctx, monkeypatch):
    fn = patch_capture(monkeypatch, {("dmesg",): Captured(1, "", "dmesg: read kernel buffer failed: Operation not permitted"),
                                      ("journalctl", "-k"): Captured(0, "kernel: hda codec error", "")})
    r = ReadLogs().run({"source": "kernel"}, ctx)
    assert r.ok and "hda codec" in r.output and fn.calls[0][0] == "dmesg" and fn.calls[1][:2] == ["journalctl", "-k"]


def test_read_logs_permission_hint(ctx, monkeypatch):
    patch_capture(monkeypatch, {("journalctl",): Captured(1, "", "Permission denied")})
    r = ReadLogs().run({}, ctx)
    assert not r.ok and "systemd-journal" in r.error


def test_read_logs_caps_lines(ctx, monkeypatch):
    fn = patch_capture(monkeypatch, {("journalctl",): Captured(0, "x", "")})
    ReadLogs().run({"lines": 100000}, ctx)
    assert "500" in fn.calls[-1]


# ---------------------------------------------------------------- devices
LSPCI = """00:1f.3 Audio device [0403]: Intel Corporation Sunrise Point-LP HD Audio [8086:9d71] (rev 21)
\tKernel driver in use: snd_hda_intel
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GP107 [10de:1c8d]
"""


def test_read_device_info_extracts_hardware_ids(ctx, monkeypatch):
    patch_capture(monkeypatch, {("lspci",): Captured(0, LSPCI, "")})
    r = ReadDeviceInfo().run({"category": "pci"}, ctx)
    assert r.ok and r.data["hardware_ids"] == ["10de:1c8d", "8086:9d71"] and "snd_hda_intel" in r.output
    assert "[0403]" not in " ".join(r.data["hardware_ids"])  # class codes are not vendor:device ids


def test_read_device_info_usb_ids(ctx, monkeypatch):
    patch_capture(monkeypatch, {("lsusb",): Captured(0, "Bus 001 Device 004: ID 0bda:5411 Realtek Hub", "")})
    assert ReadDeviceInfo().run({"category": "usb"}, ctx).data["hardware_ids"] == ["0bda:5411"]


def test_read_device_info_audio_uses_several_commands_and_tolerates_missing(ctx, monkeypatch):
    fn = patch_capture(monkeypatch, {("aplay",): Captured(0, "card 0: PCH", ""), ("pactl", "info"): Captured(0, "Default Sink: auto_null", "")})
    r = ReadDeviceInfo().run({"category": "audio"}, ctx)
    assert r.ok and "card 0" in r.output and "auto_null" in r.output
    assert {tuple(c[:2]) for c in fn.calls} >= {("aplay", "-l"), ("pactl", "info")}


def test_read_device_info_nothing_available(ctx, monkeypatch):
    patch_capture(monkeypatch, {}, have_all=False)
    r = ReadDeviceInfo().run({"category": "pci"}, ctx)
    assert not r.ok and "lspci" in r.error


def test_read_device_info_rejects_unknown_category(ctx):
    assert "one of" in ReadDeviceInfo().run({"category": "bios"}, ctx).error


# ---------------------------------------------------------------- packages
def test_read_package_state_apt_installed(ctx, monkeypatch):
    patch_capture(monkeypatch, {("dpkg-query",): Captured(0, "ii \t1.2.3-1\n", ""),
                                ("apt-cache",): Captured(0, "x:\n  Installed: 1.2.3-1\n  Candidate: 1.2.4-1\n", "")})
    r = ReadPackageState().run({"name": "alsa-utils"}, ctx)
    assert r.ok and r.data["installed"] and r.data["version"] == "1.2.3-1" and r.data["candidate"] == "1.2.4-1"
    assert "newest available: 1.2.4-1" in r.output


def test_read_package_state_apt_removed_config_files_is_not_installed(ctx, monkeypatch):
    patch_capture(monkeypatch, {("dpkg-query",): Captured(0, "rc \t1.0\n", ""), ("apt-cache",): Captured(0, "Candidate: (none)", "")})
    r = ReadPackageState().run({"name": "oldpkg"}, ctx)
    assert r.data["installed"] is False and r.data["candidate"] == ""


def test_read_package_state_unknown_package(ctx, monkeypatch):
    patch_capture(monkeypatch, {("dpkg-query",): Captured(1, "", "no packages found"), ("apt-cache",): Captured(0, "", "")})
    assert ReadPackageState().run({"name": "nothing"}, ctx).data["installed"] is False


@pytest.mark.parametrize("pm,mapping,expected", [
    ("dnf", {("rpm",): Captured(0, "1.4-2.fc40\n", "")}, ("1.4-2.fc40", True)),
    ("pacman", {("pacman",): Captured(0, "alsa-utils 1.2.9-1\n", "")}, ("1.2.9-1", True)),
    ("brew", {("brew",): Captured(0, "wget 1.24.5\n", "")}, ("1.24.5", True)),
])
def test_read_package_state_other_managers(ctx, monkeypatch, pm, mapping, expected):
    ctx.env.package_manager = pm
    patch_capture(monkeypatch, mapping)
    r = ReadPackageState().run({"name": "wget"}, ctx)
    assert (r.data["version"], r.data["installed"]) == expected


@pytest.mark.parametrize("bad", ["x; rm -rf /", "$(id)", "a b", "", "-rf", "../../etc/passwd", "a`id`"])
def test_read_package_state_rejects_bad_names(ctx, monkeypatch, bad):
    fn = patch_capture(monkeypatch, {})
    assert not ReadPackageState().run({"name": bad}, ctx).ok and fn.calls == []


def test_read_package_state_without_package_manager(ctx):
    ctx.env.package_manager = None
    assert "package manager" in ReadPackageState().run({"name": "x"}, ctx).error


# ---------------------------------------------------------------- services
SHOW = "LoadState=loaded\nActiveState=failed\nSubState=failed\nUnitFileState=enabled\nResult=exit-code\nExecMainStatus=1\nDescription=Network\n"


def test_read_service_state_failed_service_with_recent_logs(ctx, monkeypatch):
    patch_capture(monkeypatch, {("systemctl", "show"): Captured(0, SHOW, ""), ("journalctl",): Captured(0, "Jan 1 nm: config error line 3", "")})
    r = ReadServiceState().run({"name": "NetworkManager"}, ctx)
    assert r.ok and r.data["activestate"] == "failed" and r.data["found"] is True
    assert "failed (failed)" in r.output and "config error line 3" in r.output and "enabled" in r.output


def test_read_service_state_not_found(ctx, monkeypatch):
    patch_capture(monkeypatch, {("systemctl", "show"): Captured(0, "LoadState=not-found\nActiveState=inactive\n", "")})
    r = ReadServiceState().run({"name": "ghost"}, ctx)
    assert r.ok and r.data["found"] is False and "no such service" in r.output


@pytest.mark.parametrize("bad", ["x; reboot", "a b", "$(id)", "x\ny", "x'y"])
def test_read_service_state_rejects_bad_names(ctx, monkeypatch, bad):
    fn = patch_capture(monkeypatch, {})
    assert not ReadServiceState().run({"name": bad}, ctx).ok and fn.calls == []
