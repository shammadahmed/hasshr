import subprocess

import pytest

from termiai.contracts import Reversibility as R
from termiai.tools import kernel as k
from termiai.tools.kernel import (
    ListKernels,
    RebootIntoKernelOnce,
    find_menu_entry,
    grub_default_is_saved,
    installed_kernels,
    parse_grub_entries,
    previous_kernel,
    version_key,
)

GRUB_CFG = """
function gfxmode {
\tset gfxpayload="${1}"
}
menuentry 'Ubuntu' --class ubuntu --class os $menuentry_id_option 'gnulinux-simple-abc' {
\trecordfail
\tlinux\t/boot/vmlinuz-6.8.0-45-generic root=UUID=x ro quiet splash
}
submenu 'Advanced options for Ubuntu' $menuentry_id_option 'gnulinux-advanced-abc' {
\tmenuentry 'Ubuntu, with Linux 6.8.0-45-generic' --class ubuntu $menuentry_id_option 'gnulinux-6.8.0-45-generic-advanced-abc' {
\t\tlinux /boot/vmlinuz-6.8.0-45-generic
\t}
\tmenuentry 'Ubuntu, with Linux 6.8.0-45-generic (recovery mode)' --class ubuntu {
\t\tlinux /boot/vmlinuz-6.8.0-45-generic single
\t}
\tmenuentry 'Ubuntu, with Linux 6.8.0-4-generic' --class ubuntu {
\t\tlinux /boot/vmlinuz-6.8.0-4-generic
\t}
\tmenuentry 'Ubuntu, with Linux 6.8.0-40-generic' --class ubuntu {
\t\tlinux /boot/vmlinuz-6.8.0-40-generic
\t}
\tmenuentry 'Ubuntu, with Linux 6.8.0-40-generic (recovery mode)' --class ubuntu {
\t\tlinux /boot/vmlinuz-6.8.0-40-generic single
\t}
}
menuentry 'UEFI Firmware Settings' $menuentry_id_option 'uefi-firmware' {
\tfwsetup
}
"""


# ---------------------------------------------------------------- versions
def test_version_key_orders_naturally():
    vs = ["6.8.0-9-generic", "6.8.0-45-generic", "6.10.1-1-generic", "6.8.0-40-generic", "5.15.0-100-generic"]
    assert sorted(vs, key=version_key) == ["5.15.0-100-generic", "6.8.0-9-generic", "6.8.0-40-generic", "6.8.0-45-generic", "6.10.1-1-generic"]


def test_previous_kernel_is_next_older_same_flavor():
    inst = ["5.15.0-100-generic", "6.8.0-40-generic", "6.8.0-45-generic", "6.8.0-45-lowlatency", "6.9.0-1-generic"]
    assert previous_kernel(inst, "6.8.0-45-generic") == "6.8.0-40-generic"
    assert previous_kernel(inst, "6.9.0-1-generic") == "6.8.0-45-generic"
    assert previous_kernel(inst, "5.15.0-100-generic") is None


def test_previous_kernel_falls_back_to_other_flavor_when_needed():
    assert previous_kernel(["6.8.0-40-lowlatency", "6.8.0-45-generic"], "6.8.0-45-generic") == "6.8.0-40-lowlatency"


def test_installed_kernels_scans_boot(tmp_path):
    for n in ("vmlinuz-6.8.0-45-generic", "vmlinuz-6.8.0-40-generic", "vmlinuz-linux", "initrd.img-6.8.0-45-generic", "config-6.8.0-45-generic"):
        (tmp_path / n).write_text("")
    assert installed_kernels(str(tmp_path)) == ["6.8.0-40-generic", "6.8.0-45-generic"]
    assert installed_kernels(str(tmp_path / "missing")) == []


# ---------------------------------------------------------------- grub parsing
def test_parse_grub_entries_builds_submenu_paths():
    entries = {e.title: e.path for e in parse_grub_entries(GRUB_CFG)}
    assert entries["Ubuntu"] == "Ubuntu"
    assert entries["Ubuntu, with Linux 6.8.0-40-generic"] == "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-40-generic"
    assert entries["UEFI Firmware Settings"] == "UEFI Firmware Settings"  # stack unwound after the submenu


def test_find_menu_entry_matches_exact_version_and_skips_recovery():
    entries = parse_grub_entries(GRUB_CFG)
    assert find_menu_entry(entries, "6.8.0-40-generic").title == "Ubuntu, with Linux 6.8.0-40-generic"
    assert find_menu_entry(entries, "6.8.0-4-generic").title == "Ubuntu, with Linux 6.8.0-4-generic"  # not confused with -40 / -45
    assert find_menu_entry(entries, "6.8.0-4") is None or "recovery" not in find_menu_entry(entries, "6.8.0-4").title
    assert find_menu_entry(entries, "9.9.9-generic") is None


@pytest.mark.parametrize("text,expected", [
    ('GRUB_DEFAULT=saved\n', True), ('GRUB_DEFAULT="saved"\n', True), ("GRUB_DEFAULT='saved'  # keep\n", True),
    ("GRUB_DEFAULT=0\n", False), ("#GRUB_DEFAULT=saved\nGRUB_DEFAULT=0\n", False), ("", False),
    ("GRUB_DEFAULT=0\nGRUB_DEFAULT=saved\n", True), ("GRUB_DEFAULT=saved\nGRUB_DEFAULT=0\n", False),
])
def test_grub_default_is_saved(tmp_path, text, expected):
    f = tmp_path / "grub"
    f.write_text(text)
    assert grub_default_is_saved(str(f)) is expected
    assert grub_default_is_saved(str(tmp_path / "missing")) is False


# ---------------------------------------------------------------- tool wiring
@pytest.fixture
def grub_machine(monkeypatch, ctx):
    ctx.env.kernel = "6.8.0-45-generic"
    monkeypatch.setattr(k, "installed_kernels", lambda boot="/boot": ["6.8.0-4-generic", "6.8.0-40-generic", "6.8.0-45-generic"])
    monkeypatch.setattr(k, "detect_bootloader", lambda: "grub")
    monkeypatch.setattr(k, "grub_default_is_saved", lambda path="/etc/default/grub": True)
    monkeypatch.setattr(k, "_read_grub_cfg", lambda: GRUB_CFG)
    monkeypatch.setattr(k, "have", lambda p: p in {"grub-reboot", "grub-editenv", "systemctl"})
    monkeypatch.setattr(k, "ensure_admin", lambda c: None)
    return ctx


def fake_sudo(monkeypatch, env_output=None, fail_reboot=False):
    calls = []
    entry = {}

    def run(cmd, **kw):
        calls.append(cmd)
        assert cmd[:2] == ["sudo", "-n"], "privileged commands must be non-interactive sudo"
        prog = cmd[2]
        if prog == "grub-reboot":
            if fail_reboot:
                return subprocess.CompletedProcess(cmd, 1, "", "grub-reboot: error")
            entry["v"] = cmd[3]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if prog == "grub-editenv" and cmd[3] == "list":
            return subprocess.CompletedProcess(cmd, 0, env_output if env_output is not None else f"next_entry={entry.get('v')}\n", "")
        if prog == "grub-editenv" and cmd[3:] == ["-", "unset", "next_entry"]:
            entry.pop("v", None)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(subprocess, "run", run)
    return calls, entry


def test_list_kernels(grub_machine):
    r = ListKernels().run({}, grub_machine)
    assert r.ok and r.data["previous"] == "6.8.0-40-generic" and r.data["running"] == "6.8.0-45-generic"
    assert r.data["grub_default_saved"] is True and "One-time boot ready: yes" in r.output


def test_boot_once_default_previous_kernel_never_changes_default(grub_machine, monkeypatch, journal):
    calls, entry = fake_sudo(monkeypatch)
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert r.ok and r.data["kernel"] == "6.8.0-40-generic" and r.data["default_changed"] is False and r.reversibility == R.FULL
    assert entry["v"] == "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-40-generic"
    assert [c[2] for c in calls] == ["grub-reboot", "grub-editenv"]
    assert not any("set_default" in " ".join(c) or "grub-set-default" in " ".join(c) for c in calls)
    assert journal.entries[-1].undo["op"] == "bootonce"
    assert r.data["verified"] is True and "one time only" in r.output


def test_boot_once_explicit_version(grub_machine, monkeypatch):
    fake_sudo(monkeypatch)
    r = RebootIntoKernelOnce().run({"kernel_version": "6.8.0-4-generic"}, grub_machine)
    assert r.ok and r.data["menu_entry"].endswith("6.8.0-4-generic")


@pytest.mark.parametrize("kw,msg", [
    ({"kernel_version": "6.8.0-45-generic"}, "already running"),
    ({"kernel_version": "1.2.3-generic"}, "not installed"),
])
def test_boot_once_validates_target(grub_machine, monkeypatch, kw, msg):
    calls, _ = fake_sudo(monkeypatch)
    r = RebootIntoKernelOnce().run(kw, grub_machine)
    assert not r.ok and msg in r.error and calls == []


def test_boot_once_no_older_kernel(grub_machine, monkeypatch):
    monkeypatch.setattr(k, "installed_kernels", lambda boot="/boot": ["6.8.0-45-generic"])
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert not r.ok and "No older kernel" in r.error and "Advanced options" in r.error


def test_boot_once_refuses_when_grub_default_not_saved_and_does_not_edit_bootloader(grub_machine, monkeypatch):
    monkeypatch.setattr(k, "grub_default_is_saved", lambda path="/etc/default/grub": False)
    calls, _ = fake_sudo(monkeypatch)
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert not r.ok and "GRUB_DEFAULT" in r.error and "will not make silently" in r.error and calls == []


def test_boot_once_unsupported_bootloader_gives_manual_steps(grub_machine, monkeypatch):
    monkeypatch.setattr(k, "detect_bootloader", lambda: "systemd-boot")
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert not r.ok and "systemd-boot" in r.error and "Advanced options" in r.error


def test_boot_once_non_linux(grub_machine):
    grub_machine.env.os = "macos"
    assert "only supported on Linux" in RebootIntoKernelOnce().run({}, grub_machine).error
    assert "only supported on Linux" in ListKernels().run({}, grub_machine).error


def test_boot_once_grub_reboot_failure_changes_nothing(grub_machine, monkeypatch, journal):
    fake_sudo(monkeypatch, fail_reboot=True)
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert not r.ok and "Nothing was changed" in r.error and journal.entries == []


def test_boot_once_unverifiable_request_is_cancelled(grub_machine, monkeypatch, journal):
    calls, entry = fake_sudo(monkeypatch, env_output="next_entry=something-else\n")
    r = RebootIntoKernelOnce().run({}, grub_machine)
    assert not r.ok and "cancelled" in r.error and "v" not in entry and journal.entries == []
    assert calls[-1][3:] == ["-", "unset", "next_entry"]


def test_reboot_requires_saved_case_state_and_is_labelled_none(grub_machine, monkeypatch):
    calls, _ = fake_sudo(monkeypatch)
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: popen.append(cmd))
    tool = RebootIntoKernelOnce()
    assert tool.reversibility_for({"reboot": True}, grub_machine) == R.NONE
    assert tool.reversibility_for({}, grub_machine) == R.FULL
    r = tool.run({"reboot": True}, grub_machine)
    assert not r.ok and "case state must be saved" in r.error and calls == [] and popen == []  # nothing set, nothing rebooted
    grub_machine.extras["ready_to_reboot"] = True
    r = tool.run({"reboot": True}, grub_machine)
    assert r.ok and r.data["rebooting"] and popen == [["sudo", "-n", "systemctl", "reboot"]]


def test_preview_surfaces_prerequisite_problems_without_acting(grub_machine, monkeypatch):
    calls, _ = fake_sudo(monkeypatch)
    monkeypatch.setattr(k, "grub_default_is_saved", lambda path="/etc/default/grub": False)
    pv = RebootIntoKernelOnce().preview({}, grub_machine)
    assert pv.needs_admin and any("GRUB_DEFAULT" in w for w in pv.warnings) and calls == []
    monkeypatch.setattr(k, "grub_default_is_saved", lambda path="/etc/default/grub": True)
    pv = RebootIntoKernelOnce().preview({"reboot": True}, grub_machine)
    assert "6.8.0-40-generic" in pv.detail and pv.reversibility == R.NONE and any("resume" in w for w in pv.warnings)
