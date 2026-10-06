"""Static analysis of shell command strings, used by `run_shell`.

This answers four questions BEFORE a command runs:

  1. Does it need admin rights (sudo/doas/pkexec/su)?   -> TTY prompt handling
  2. Does it start an interactive program (nano, vim...)?  -> refused (F-37)
  3. Is it read-only?                                       -> Safe-to-run label
  4. How reversible is it (Full / Partial / None)?          -> shown to the user (F-47)

IMPORTANT: this is *labelling*, not the security boundary. The Risk Engine (M2)
is the authority on whether a command may run. Here we only decide how to
describe the command and how to execute it safely. When unsure we pick the
more cautious label ("never promise an undo that cannot happen").
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Optional

from ..contracts import Reversibility

PRIVILEGE_PROGRAMS = {"sudo", "doas", "pkexec", "su", "run0"}
SHELL_PROGRAMS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "ash"}
INTERACTIVE_PROGRAMS = {
    "nano", "vi", "vim", "nvim", "emacs", "pico", "ed", "joe", "micro", "less", "more",
    "most", "man", "top", "htop", "btop", "watch", "tmux", "screen", "ranger", "mc",
    "nmtui", "alsamixer", "ncdu", "visudo", "crontab",  # crontab -e opens an editor
}
# Programs that merely wrap another command; analysis continues with the inner command.
_WRAPPERS = {"env", "nohup", "time", "command", "exec", "nice", "ionice", "stdbuf", "timeout", "xargs", "builtin"}
_SUDO_OPTS_WITH_ARG = {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t", "--user", "--group", "--host", "--prompt", "--chdir", "--role", "--type", "--other-user"}
_SEPARATORS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NULL_TARGETS = {"/dev/null", "/dev/stderr", "/dev/stdout", "/dev/tty"}

_KERNEL_PKG = re.compile(r"(linux-(image|headers|modules|generic|firmware)|^kernel|^nvidia|dkms|^grub|firmware|^linux-)", re.I)

# --- read-only knowledge ------------------------------------------------------
_RO_ALWAYS = {
    "ls", "cat", "head", "tail", "wc", "df", "du", "stat", "file", "pwd", "echo", "printf", "whoami",
    "id", "uname", "hostname", "uptime", "free", "date", "which", "type", "whereis", "lspci", "lsusb",
    "lsmod", "lsblk", "lscpu", "lshw", "dmesg", "printenv", "ps", "pgrep", "grep", "egrep", "fgrep",
    "rg", "uniq", "cut", "tr", "diff", "cmp", "md5sum", "sha1sum", "sha256sum", "tree", "readlink",
    "realpath", "basename", "dirname", "nproc", "lsof", "ss", "netstat", "ifconfig", "ping", "nslookup",
    "dig", "host", "getent", "locale", "findmnt", "fc-list", "modinfo", "apt-cache", "lsb_release",
    "arch", "groups", "last", "w", "who", "true", "false", "test", "[", "sleep", "lsinitramfs",
    "inxi", "hwinfo", "dmidecode", "sensors", "upower", "xrandr", "ldd", "nm", "strings",
    "od", "hexdump", "xxd", "column", "jq", "bc", "expr", "seq", "rfkill",
}


def _ro_find(args: list[str]) -> bool:
    return not any(a in {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"} for a in args)


def _ro_sort(args: list[str]) -> bool:
    return not any(a == "-o" or a.startswith("--output") or (a.startswith("-o") and not a.startswith("--")) for a in args)


def _ro_sed(args: list[str]) -> bool:
    return not any(a == "-i" or a.startswith("-i") or a == "--in-place" or a.startswith("--in-place") for a in args)


def _ro_awk(args: list[str]) -> bool:
    joined = " ".join(args)
    return "system(" not in joined and ">" not in joined and "|" not in joined


def _first_non_option(args: list[str]) -> Optional[str]:
    for a in args:
        if not a.startswith("-"):
            return a
    return None


def _ro_sub(allowed: set[str]):
    def check(args: list[str]) -> bool:
        sub = _first_non_option(args)
        return sub in allowed
    return check


def _ro_sub_or_none(allowed: set[str]):
    def check(args: list[str]) -> bool:
        sub = _first_non_option(args)
        return sub is None or sub in allowed
    return check


def _ro_journalctl(args: list[str]) -> bool:
    bad = ("--vacuum", "--rotate", "--flush", "--sync", "--relinquish", "--smart-relinquish", "--setup-keys", "--update-catalog")
    return not any(a.startswith(bad) for a in args)


def _ro_pactl(args: list[str]) -> bool:
    sub = _first_non_option(args)
    return sub in {"list", "info", "stat", "get-default-sink", "get-default-source", "get-sink-volume", "get-source-volume", "get-sink-mute", "get-source-mute", "version"}


def _ro_aplay(args: list[str]) -> bool:
    return any(a in {"-l", "-L", "--list-devices", "--list-pcms", "-V"} for a in args)


def _ro_amixer(args: list[str]) -> bool:
    sub = _first_non_option(args)
    return sub in {None, "get", "sget", "scontents", "contents", "controls", "info"}


def _ro_ip(args: list[str]) -> bool:
    words = [a for a in args if not a.startswith("-")]
    verbs = {"add", "del", "delete", "set", "flush", "replace", "change", "append", "route add"}
    return not any(w in verbs for w in words[1:2]) and not any(w in verbs for w in words)


def _ro_sysctl(args: list[str]) -> bool:
    return "-w" not in args and "--write" not in args and not any("=" in a for a in args) and "-p" not in args and "--system" not in args


def _ro_mount(args: list[str]) -> bool:
    return not [a for a in args if not a.startswith("-")]


def _ro_git(args: list[str]) -> bool:
    sub = _first_non_option(args)
    if sub in {"status", "log", "diff", "show", "rev-parse", "describe", "blame", "shortlog", "ls-files", "ls-tree", "grep", "cat-file"}:
        return True
    if sub in {"branch", "remote", "tag", "config"}:
        rest = args[args.index(sub) + 1:]
        return all(a in {"-v", "-vv", "-a", "-r", "--list", "-l", "--show-current", "--get", "--get-all"} or a == "show" for a in rest) if rest else sub != "config"
    return False


def _ro_tar(args: list[str]) -> bool:
    """tar is read-only only when listing (t) and not extracting/creating/updating."""
    if "--list" in args:
        return True
    flags = ""
    for a in args:
        if a.startswith("--"):
            continue
        if a.startswith("-"):
            flags += a[1:]
        elif not flags:
            flags += a  # old-style "tar tf file"
            break
        else:
            break
    return "t" in flags and not any(c in flags for c in "xcru")


def _ro_nmcli(args: list[str]) -> bool:
    words = [a for a in args if not a.startswith("-")]
    return bool(words) and words[0] in {"general", "device", "connection", "radio", "networking"} and (len(words) == 1 or words[1] in {"status", "show", "list", "wifi", "info"}) and not any(w in {"up", "down", "delete", "add", "modify", "connect", "disconnect", "reload", "on", "off"} for w in words[1:])


_RO_CONDITIONAL = {
    "find": _ro_find,
    "sort": _ro_sort,
    "sed": _ro_sed,
    "awk": _ro_awk,
    "gawk": _ro_awk,
    "journalctl": _ro_journalctl,
    "pactl": _ro_pactl,
    "aplay": _ro_aplay,
    "arecord": _ro_aplay,
    "amixer": _ro_amixer,
    "ip": _ro_ip,
    "sysctl": _ro_sysctl,
    "mount": _ro_mount,
    "git": _ro_git,
    "nmcli": _ro_nmcli,
    "systemctl": _ro_sub({"status", "is-active", "is-enabled", "is-failed", "show", "list-units", "list-unit-files", "list-sockets", "list-timers", "list-dependencies", "cat", "get-default"}),
    "loginctl": _ro_sub({"list-sessions", "list-users", "show-session", "show-user", "session-status", "user-status", "show-seat", "list-seats"}),
    "timedatectl": _ro_sub_or_none({"status", "show", "list-timezones"}),
    "hostnamectl": _ro_sub_or_none({"status", "show"}),
    "apt": _ro_sub({"list", "show", "search", "policy", "depends", "rdepends"}),
    "apt-get": _ro_sub({"check"}),
    "dpkg": lambda a: any(x in {"-l", "-L", "-s", "-S", "-C", "--list", "--listfiles", "--status", "--search", "--audit", "--get-selections", "-V", "--verify"} for x in a),
    "dpkg-query": lambda a: True,
    "dnf": _ro_sub({"list", "info", "search", "repolist", "provides", "repoquery", "history", "check-update", "updateinfo"}),
    "yum": _ro_sub({"list", "info", "search", "repolist", "provides", "check-update"}),
    "rpm": lambda a: any(x.startswith("-q") or x in {"--query", "-V", "--verify"} for x in a),
    "pacman": lambda a: any(x.startswith("-Q") or x.startswith("-Ss") or x.startswith("-Si") for x in a),
    "brew": _ro_sub({"list", "info", "search", "outdated", "deps", "uses", "leaves", "--version", "config", "doctor"}),
    "snap": _ro_sub({"list", "info", "find", "version"}),
    "flatpak": _ro_sub({"list", "info", "search", "remote-ls"}),
    "pip": _ro_sub({"list", "show", "freeze", "check", "search", "--version"}),
    "pip3": _ro_sub({"list", "show", "freeze", "check", "search"}),
    "wpctl": _ro_sub({"status", "inspect", "get-volume"}),
    "pw-cli": _ro_sub({"ls", "list-objects", "info", "dump"}),
    "rfkill": _ro_sub_or_none({"list"}),
    "lsattr": lambda a: True,
    "getfacl": lambda a: True,
    "curl": lambda a: not any(x in {"-o", "-O", "--output", "--remote-name", "-T", "--upload-file", "-d", "--data", "-X"} or x.startswith(("--output", "--data", "--upload")) for x in a),
    "wget": lambda a: any(x in {"-O-", "--spider", "-qO-"} for x in a) or ("-O" in a and a[a.index("-O") + 1:a.index("-O") + 2] == ["-"]),
    "env": lambda a: not [x for x in a if not x.startswith("-") and "=" not in x],
    "winget": _ro_sub({"list", "show", "search", "--version"}),
    "choco": _ro_sub({"list", "search", "info", "outdated"}),
    "tar": lambda a: _ro_tar(a),
}

# --- reversibility knowledge ------------------------------------------------
_NONE_PROGRAMS = {
    "rm", "shred", "dd", "wipefs", "fdisk", "sfdisk", "gdisk", "sgdisk", "parted", "cfdisk", "mke2fs",
    "grub-install", "update-grub", "update-grub2", "grub-mkconfig", "efibootmgr", "flashrom", "fwupdmgr",
    "fwupdtool", "shutdown", "reboot", "poweroff", "halt", "userdel", "passwd", "chpasswd", "truncate",
    "mkswap", "lvremove", "vgremove", "pvremove", "cryptsetup", "bootctl", "kexec", "srm", "unlink",
    "dkms",
}
_PARTIAL_PROGRAMS = {
    "cp", "mv", "chmod", "chown", "chgrp", "ln", "rmdir", "tar", "unzip", "zip", "gzip", "gunzip", "bzip2",
    "xz", "install", "rsync", "patch", "useradd", "usermod", "groupadd", "groupmod", "groupdel", "adduser",
    "deluser", "ufw", "iptables", "ip6tables", "nft", "firewall-cmd", "update-initramfs", "dracut",
    "mkinitcpio", "depmod", "update-alternatives", "ldconfig", "locale-gen", "timedatectl", "hostnamectl",
    "setfacl", "chattr", "mount", "umount", "swapon", "swapoff", "grub-reboot", "grub-set-default",
    "gsettings", "dconf", "pactl", "amixer", "wpctl", "nmcli", "sysctl", "tee", "sed", "pip", "pip3",
    "npm", "yarn", "pnpm", "cargo", "gem", "go", "snap", "flatpak", "crontab",
}
_FULL_PROGRAMS = {"mkdir", "touch", "systemctl", "modprobe", "insmod", "rmmod", "apt-mark", "hash", "cd", "export", "alias", "kill", "pkill", "killall", "xdg-open", "open", "sleep", "mktemp", "setxkbmap", "xset", "service", "journalctl", "loginctl", "udevadm"}
_PKG_PROGRAMS = {"apt", "apt-get", "aptitude", "dnf", "yum", "zypper", "pacman", "apk", "brew", "port", "winget", "choco", "scoop", "dpkg", "rpm", "snap"}
_PKG_MUTATING = {"install", "remove", "purge", "uninstall", "upgrade", "dist-upgrade", "full-upgrade", "autoremove", "reinstall", "update", "add", "del", "erase", "-s", "-r", "-u", "-d", "-i", "-e", "--install", "--remove", "--purge"}


@dataclass
class Segment:
    program: str
    args: list[str]
    via_privilege: Optional[str] = None  # "sudo", "doas", ...


@dataclass
class ShellAnalysis:
    segments: list[Segment] = field(default_factory=list)
    redirect_targets: list[str] = field(default_factory=list)
    parse_failed: bool = False
    pipes_to_shell: bool = False
    read_only: bool = False
    needs_admin: bool = False
    privilege_stdin: bool = False  # sudo -S / --stdin (password via stdin)
    interactive_programs: list[str] = field(default_factory=list)
    reversibility: Reversibility = Reversibility.PARTIAL
    reasons: list[str] = field(default_factory=list)


def _tokenise(command: str) -> list[str]:
    lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _is_punct(tok: str) -> bool:
    return bool(tok) and all(c in "();<>|&" for c in tok)


def _split(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    """Split tokens into command segments; also return redirect targets."""
    segments: list[list[str]] = [[]]
    targets: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_punct(tok):
            if ">" in tok or "<" in tok:
                # Redirect operator: next token is the target (unless fd-dup like >&2).
                has_target = i + 1 < len(tokens) and not tok.endswith("&")
                if has_target:
                    target = tokens[i + 1]
                    if ">" in tok and target not in _NULL_TARGETS and not _is_punct(target):
                        targets.append(target)
                    i += 1
            else:
                if segments[-1]:
                    segments.append([])
        else:
            segments[-1].append(tok)
        i += 1
    return [s for s in segments if s], targets


def _unwrap(tokens: list[str]) -> Optional[Segment]:
    """Strip env assignments and wrappers (incl. sudo) to find the real program."""
    i = 0
    privilege: Optional[str] = None
    last_wrapper: Optional[str] = None
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if _ASSIGNMENT.match(tok):
            i += 1
            continue
        base = tok.rsplit("/", 1)[-1]
        if base in PRIVILEGE_PROGRAMS:
            privilege = privilege or base
            i += 1
            while i < n and tokens[i].startswith("-"):
                opt = tokens[i]
                i += 1
                if opt in _SUDO_OPTS_WITH_ARG and i < n:
                    i += 1
            continue
        if base in _WRAPPERS:
            last_wrapper = base
            i += 1
            while i < n and (tokens[i].startswith("-") or _ASSIGNMENT.match(tokens[i]) or tokens[i].isdigit()):
                i += 1
            continue
        return Segment(program=base, args=tokens[i + 1:], via_privilege=privilege)
    if privilege:
        return Segment(program=privilege, args=[], via_privilege=privilege)
    if last_wrapper:  # e.g. a bare `env`, which just prints the environment
        return Segment(program=last_wrapper, args=[])
    return None


def _analyse_into(command: str, out: ShellAnalysis, depth: int = 0) -> None:
    try:
        tokens = _tokenise(command)
    except ValueError:
        out.parse_failed = True
        out.reasons.append("Could not parse the command (unbalanced quotes?)")
        if re.search(r"(^|[;&|(\s])(sudo|doas|pkexec|su)(\s|$)", command):
            out.needs_admin = True
        return
    raw_segments, targets = _split(tokens)
    out.redirect_targets.extend(targets)
    prev_program: Optional[str] = None
    for idx, seg_tokens in enumerate(raw_segments):
        seg = _unwrap(seg_tokens)
        if seg is None:
            continue
        if seg.via_privilege:
            out.needs_admin = True
            if any(a in {"-S", "--stdin"} or (a.startswith("-") and not a.startswith("--") and "S" in a[1:] and seg.program == seg.via_privilege) for a in seg_tokens[:6]) and seg.via_privilege == "sudo":
                out.privilege_stdin = True
        if seg.program in PRIVILEGE_PROGRAMS and not seg.args:
            out.needs_admin = True
        if seg.program in INTERACTIVE_PROGRAMS and not (seg.program == "crontab" and any(a in {"-l", "-r"} for a in seg.args)):
            out.interactive_programs.append(seg.program)
        if seg.program in SHELL_PROGRAMS:
            script = None
            for j, a in enumerate(seg.args):
                if a.startswith("-") and not a.startswith("--") and "c" in a[1:] and j + 1 < len(seg.args):
                    script = seg.args[j + 1]
                    break
            if script is not None and depth < 3:
                _analyse_into(script, out, depth + 1)
                prev_program = seg.program
                continue
            if idx > 0 and prev_program in {"curl", "wget", "fetch"}:
                out.pipes_to_shell = True
        out.segments.append(seg)
        prev_program = seg.program


def _is_read_only_segment(seg: Segment) -> bool:
    if seg.program in _RO_ALWAYS:
        return True
    check = _RO_CONDITIONAL.get(seg.program)
    return bool(check and check(seg.args))


def _worst(a: Reversibility, b: Reversibility) -> Reversibility:
    order = {Reversibility.FULL: 0, Reversibility.PARTIAL: 1, Reversibility.NONE: 2}
    return a if order[a] >= order[b] else b


def _segment_reversibility(seg: Segment) -> tuple[Reversibility, str]:
    p, args = seg.program, seg.args
    words = [a for a in args if not a.startswith("-")]
    if p in _PKG_PROGRAMS:
        sub = words[0] if words else None
        flags = {a for a in args if a.startswith("-")}
        mutating = (sub in _PKG_MUTATING) or bool(flags & {"-s", "-r", "-u", "-d", "-i", "-e", "--install", "--remove", "--purge"} and p in {"dpkg", "rpm"}) or (p == "pacman" and any(a.startswith(("-S", "-R", "-U")) and not a.startswith(("-Ss", "-Si", "-Sy")) for a in args))
        if p in {"dpkg", "rpm"} and not mutating:
            return Reversibility.FULL, ""
        if sub == "update" and p in {"apt", "apt-get"}:
            return Reversibility.FULL, "Refreshes the package list only"
        if mutating:
            pkgs = words[1:] if sub else words
            if any(_KERNEL_PKG.search(w) for w in pkgs):
                return Reversibility.NONE, "Kernel, driver or firmware packages cannot be cleanly undone"
            if sub in {"upgrade", "dist-upgrade", "full-upgrade"} and not pkgs:
                return Reversibility.NONE, "A full upgrade may replace the kernel or drivers"
            return Reversibility.PARTIAL, "Package changes can be reversed, but settings and dependencies may differ"
        return Reversibility.FULL, ""
    if p == "git":
        sub = _first_non_option(args)
        if sub in {"push", "clean", "gc", "filter-branch"} or (sub == "reset" and "--hard" in args) or (sub == "branch" and ("-D" in args)):
            return Reversibility.NONE, "This git command can discard work permanently"
        return Reversibility.PARTIAL, "Git changes are usually recoverable from history"
    if p == "systemctl":
        sub = _first_non_option(args)
        if sub in {"reboot", "poweroff", "halt", "kexec", "suspend", "hibernate"}:
            return Reversibility.NONE, "Restarts or powers off the machine; unsaved work is lost"
        return Reversibility.FULL, "Service state can be switched back"
    if p == "find" and any(a in {"-delete", "-fprint", "-fprintf", "-fls"} for a in args):
        return Reversibility.NONE, "find -delete removes data permanently (use delete_to_trash instead)"
    if p == "sed" and not _ro_sed(args):
        return Reversibility.NONE, "In-place edit without a backup (use edit_file instead)"
    if p in {"curl", "wget"}:
        return Reversibility.PARTIAL, "Downloads create files; existing files may be overwritten"
    if p in _NONE_PROGRAMS or p.startswith("mkfs"):
        if p == "rm":
            return Reversibility.NONE, "Deleted data cannot be restored (use delete_to_trash instead)"
        return Reversibility.NONE, f"'{p}' makes changes that cannot be undone"
    if p in _PARTIAL_PROGRAMS:
        return Reversibility.PARTIAL, f"'{p}' changes cannot be fully tracked; prefer a typed tool if one fits"
    if p in _FULL_PROGRAMS:
        return Reversibility.FULL, ""
    return Reversibility.PARTIAL, f"Effects of '{p}' are unknown, so undo may be incomplete"


def analyse_command(command: str, cwd: Optional[str] = None) -> ShellAnalysis:
    """Analyse a shell command string. Never raises."""
    import os

    out = ShellAnalysis()
    _analyse_into(command, out)
    if out.parse_failed:
        out.reversibility = Reversibility.NONE
        out.reasons.append("Treated as irreversible because it could not be analysed")
        out.read_only = False
        return out

    rev = Reversibility.FULL
    all_ro = bool(out.segments)
    for seg in out.segments:
        if _is_read_only_segment(seg):
            continue
        all_ro = False
        seg_rev, reason = _segment_reversibility(seg)
        rev = _worst(rev, seg_rev)
        if reason and reason not in out.reasons:
            out.reasons.append(reason)
    if out.pipes_to_shell:
        rev = Reversibility.NONE
        all_ro = False
        out.reasons.append("Runs downloaded code directly; effects are unknown and not undoable")
    for target in out.redirect_targets:
        all_ro = False
        path = target if os.path.isabs(target) else os.path.join(cwd or os.getcwd(), os.path.expanduser(target))
        path = os.path.expanduser(path)
        if os.path.exists(path):
            rev = _worst(rev, Reversibility.NONE)
            out.reasons.append(f"Overwrites or appends to existing file '{target}' with no backup (use edit_file instead)")
        else:
            rev = _worst(rev, Reversibility.PARTIAL)
            out.reasons.append(f"Creates file '{target}' (use edit_file for tracked changes)")
    out.read_only = all_ro
    out.reversibility = Reversibility.FULL if out.read_only else rev
    return out


def uses_privilege_escalation(command: str) -> bool:
    return analyse_command(command).needs_admin


def is_read_only(command: str) -> bool:
    return analyse_command(command).read_only
