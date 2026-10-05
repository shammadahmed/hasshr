from pathlib import Path

import pytest

from termiai.platform import (
    EnvInfo,
    LinuxAdapter,
    MacAdapter,
    WindowsAdapter,
    detect_environment,
    get_adapter,
)
from termiai.platform.detect import parse_os_release

UBUNTU = '''NAME="Ubuntu"
VERSION_ID="24.04"
PRETTY_NAME="Ubuntu 24.04.1 LTS"
ID=ubuntu
# a comment
'''


def test_parse_os_release():
    d = parse_os_release(UBUNTU)
    assert d["NAME"] == "Ubuntu" and d["VERSION_ID"] == "24.04" and d["ID"] == "ubuntu"
    assert "#" not in "".join(d)


def test_parse_os_release_ignores_garbage():
    assert parse_os_release("\n\nnot a pair\n=x\n") == {}


def test_detect_environment_never_raises_and_has_basics():
    env = detect_environment()
    assert env.os in {"linux", "macos", "windows"}
    assert env.kernel and env.shell and env.home
    assert "OS:" in env.summary()


def test_summary_mentions_distro_and_package_manager(env):
    s = env.summary()
    assert "Ubuntu 24.04" in s and "apt" in s and "6.8.0-45-generic" in s


def test_summary_without_package_manager():
    s = EnvInfo(os="linux").summary()
    assert "none detected" in s


@pytest.mark.parametrize("os_name,cls", [("linux", LinuxAdapter), ("macos", MacAdapter), ("windows", WindowsAdapter)])
def test_get_adapter_selects_by_os(os_name, cls):
    assert isinstance(get_adapter(EnvInfo(os=os_name, home="/h")), cls)


def test_linux_adapter_commands(env):
    a = LinuxAdapter(env)
    assert a.shell_argv("ls")[-2:] == ["-c", "ls"]
    assert a.open_command("https://x.y") == ["xdg-open", "https://x.y"]
    assert a.elevate_prefix() == ["sudo"]
    assert a.default_downloads() == Path(env.home) / "Downloads"
    assert a.is_system_path(Path("/etc/hosts"))
    assert not a.is_system_path(Path(env.home) / "notes.txt")


def test_linux_adapter_root_needs_no_prefix():
    a = LinuxAdapter(EnvInfo(os="linux", is_root=True, home="/root"))
    assert a.elevate_prefix() == []


def test_xdg_download_dir_is_respected(env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(tmp_path / "dl"))
    assert LinuxAdapter(env).default_downloads() == tmp_path / "dl"


def test_mac_adapter(env):
    a = MacAdapter(EnvInfo(os="macos", home="/Users/x"))
    assert a.open_command("f") == ["open", "f"]
    assert a.clipboard_commands()[0][0] == ["pbcopy"]
    assert a.is_system_path(Path("/System/Library"))


def test_windows_adapter():
    a = WindowsAdapter(EnvInfo(os="windows", shell="cmd", home="C:/Users/x"))
    assert a.elevate_prefix() == []  # no sudo on Windows
    assert a.open_command("https://x")[:3] == ["cmd", "/c", "start"]
    assert a.shell_argv("dir")[1] == "/c"
    assert a.clipboard_commands()[0][0] == ["clip"]


def test_windows_adapter_uses_powershell_when_detected(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/ps/powershell.exe" if name in {"powershell", "pwsh"} else None)
    a = WindowsAdapter(EnvInfo(os="windows", shell="powershell", home="C:/Users/x"))
    assert "-Command" in a.shell_argv("Get-Date")
