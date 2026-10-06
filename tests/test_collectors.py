
import pytest

from hasshr.platform import EnvInfo
from hasshr.tools import collectors as col
from hasshr.tools.files import sha256_file
from hasshr.tools.procutil import Captured


def patch_capture(monkeypatch, mapping):
    def fn(argv, timeout=30):
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        return None

    monkeypatch.setattr(col, "try_capture", fn)


def test_collect_packages_apt_only_counts_installed(monkeypatch):
    out = "bash\t5.2\tii \nold\t1.0\trc \nvim\t9.0\tii \nbroken line\n"
    patch_capture(monkeypatch, {("dpkg-query",): Captured(0, out, "")})
    assert col.collect_packages(EnvInfo(os="linux", package_manager="apt")) == {"bash": "5.2", "vim": "9.0"}


@pytest.mark.parametrize("pm,mapping,expected", [
    ("dnf", {("rpm",): Captured(0, "bash\t5.2-1\nvim\t9.0-2\n", "")}, {"bash": "5.2-1", "vim": "9.0-2"}),
    ("pacman", {("pacman",): Captured(0, "bash 5.2\nvim 9.0\n", "")}, {"bash": "5.2", "vim": "9.0"}),
    ("brew", {("brew",): Captured(0, "wget 1.24 1.25\n", "")}, {"wget": "1.24 1.25"}),
])
def test_collect_packages_other_managers(monkeypatch, pm, mapping, expected):
    patch_capture(monkeypatch, mapping)
    assert col.collect_packages(EnvInfo(os="linux", package_manager=pm)) == expected


def test_collect_packages_unavailable_returns_empty(monkeypatch):
    patch_capture(monkeypatch, {})
    assert col.collect_packages(EnvInfo(os="linux", package_manager="apt")) == {}
    assert col.collect_packages(EnvInfo(os="linux", package_manager=None)) == {}


def test_collect_services_merges_enabled_and_active(monkeypatch):
    files = "ssh.service enabled enabled\ncups.service disabled enabled\nfoo.socket enabled enabled\n"
    units = "ssh.service loaded active running OpenBSD Secure Shell\nnm.service loaded failed failed NetworkManager\n"
    patch_capture(monkeypatch, {("systemctl", "list-unit-files"): Captured(0, files, ""), ("systemctl", "list-units"): Captured(0, units, "")})
    s = col.collect_services(EnvInfo(os="linux"))
    assert s["ssh.service"] == {"enabled": "enabled", "active": "active/running"}
    assert s["cups.service"] == {"enabled": "disabled", "active": "unknown"}
    assert s["nm.service"] == {"enabled": "unknown", "active": "failed/failed"}
    assert "foo.socket" not in s


def test_collect_services_non_linux():
    assert col.collect_services(EnvInfo(os="macos")) == {}


def test_collect_module_params_from_fake_sysfs(tmp_path):
    (tmp_path / "snd_hda_intel/parameters").mkdir(parents=True)
    (tmp_path / "snd_hda_intel/parameters/model").write_text("auto\n")
    (tmp_path / "snd_hda_intel/parameters/power_save").write_text("1\n")
    (tmp_path / "noparams").mkdir()
    (tmp_path / "other/parameters").mkdir(parents=True)
    (tmp_path / "other/parameters/long").write_text("x" * 1000)
    allm = col.collect_module_params(base=tmp_path)
    assert allm["snd_hda_intel"] == {"model": "auto", "power_save": "1"} and "noparams" not in allm
    assert len(allm["other"]["long"]) == col.MAX_PARAM_VALUE
    assert list(col.collect_module_params(["snd_hda_intel", "missing"], base=tmp_path)) == ["snd_hda_intel"]
    assert col.collect_module_params(base=tmp_path / "nope") == {}


def test_collect_file_hashes_globs_and_ignores_missing(tmp_path):
    d = tmp_path / "modprobe.d"
    d.mkdir()
    (d / "a.conf").write_text("a")
    (d / "b.conf").write_text("b")
    (tmp_path / "grub").write_text("g")
    got = col.collect_file_hashes([str(d / "*"), str(tmp_path / "grub"), str(tmp_path / "ghost*")])
    assert set(got) == {str(d / "a.conf"), str(d / "b.conf"), str(tmp_path / "grub")}
    assert got[str(d / "a.conf")] == sha256_file(d / "a.conf")
    assert col.collect_file_hashes([]) == {}


def test_hash_changes_when_file_changes(tmp_path):
    f = tmp_path / "f.conf"
    f.write_text("v1")
    before = col.collect_file_hashes([str(f)])
    f.write_text("v2")
    assert col.collect_file_hashes([str(f)]) != before


def test_collect_kernel_has_release():
    k = col.collect_kernel()
    assert k["release"] and (isinstance(k.get("modules_loaded", []), list))


def test_collect_all_shape_and_unavailable_notes(monkeypatch, tmp_path):
    patch_capture(monkeypatch, {})
    f = tmp_path / "x.conf"
    f.write_text("1")
    snap = col.collect_all(EnvInfo(os="linux", package_manager="apt"), tracked_files=[str(f)], modules=[])
    assert set(snap) == {"packages", "services", "module_params", "file_hashes", "kernel", "unavailable"}
    assert snap["file_hashes"] == {str(f): sha256_file(f)} and snap["unavailable"] == ["packages", "services"]


def test_snapshots_are_json_serialisable_and_diffable(monkeypatch, tmp_path):
    import json

    patch_capture(monkeypatch, {("dpkg-query",): Captured(0, "bash\t5.2\tii \n", "")})
    f = tmp_path / "x.conf"
    f.write_text("1")
    env = EnvInfo(os="linux", package_manager="apt")
    a = col.collect_all(env, tracked_files=[str(f)], modules=[])
    f.write_text("2")
    b = col.collect_all(env, tracked_files=[str(f)], modules=[])
    json.dumps(a), json.dumps(b)
    changed = {p for p in a["file_hashes"] if a["file_hashes"][p] != b["file_hashes"].get(p)}
    assert changed == {str(f)}
