"""Previous-kernel helper (F-50): list installed kernels and boot one ONCE.

Used by Troubleshooting Cases to test "is this a kernel regression?" without
changing the machine's default boot entry. Supported: GRUB (grub-reboot /
grub2-reboot). Other bootloaders get a clear error plus manual instructions.

Design rules:
  * Never changes the default boot entry. `grub-reboot` only sets `next_entry`
    in grubenv, which GRUB clears after one boot. The undo is
    `grub-editenv - unset next_entry`.
  * Never edits the bootloader configuration. `grub-reboot` is ignored unless
    GRUB_DEFAULT=saved; if that is not set we STOP and explain, because
    changing it is a separate, non-reversible bootloader change that needs the
    user's explicit approval.
  * Never reboots on its own. Rebooting needs reboot=true AND the Cases owner
    (M5) setting ctx.extras["ready_to_reboot"]=True after persisting the case
    state, so `hasshr resume` can continue afterwards (F-42).
  * Requires administrator rights, obtained directly from the user (F-24).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..contracts import Reversibility, ToolResult
from .base import Preview, Tool, ToolContext, ToolError
from .privilege import ensure_admin
from .procutil import have

GRUB_CFG_PATHS = ["/boot/grub/grub.cfg", "/boot/grub2/grub.cfg", "/boot/efi/EFI/fedora/grub.cfg"]
_TOKEN = re.compile(r"\d+|[A-Za-z]+")
_MENU_RE = re.compile(r"""^\s*(menuentry|submenu)\s+(['"])(.*?)\2""")


# ---- version helpers ----------------------------------------------------------
def version_key(version: str) -> tuple[tuple[int, Any], ...]:
    """Natural sort key: '6.8.0-45-generic' -> ((0,6),(0,8),(0,0),(0,45),(1,'generic'))."""
    return tuple((0, int(t)) if t.isdigit() else (1, t.lower()) for t in _TOKEN.findall(version))


def _flavor(version: str) -> str:
    words = [t for t in _TOKEN.findall(version) if not t.isdigit()]
    return words[-1].lower() if words else ""


def installed_kernels(boot_dir: str = "/boot") -> list[str]:
    """Kernel releases that have a vmlinuz image in /boot, oldest first."""
    versions = []
    try:
        for p in Path(boot_dir).glob("vmlinuz-*"):
            v = p.name[len("vmlinuz-"):]
            if v and re.match(r"^\d", v):
                versions.append(v)
    except OSError:
        return []
    return sorted(set(versions), key=version_key)


def previous_kernel(installed: list[str], running: str) -> Optional[str]:
    """The newest installed kernel that is older than the running one,
    preferring the same flavor (e.g. '-generic')."""
    older = [k for k in installed if version_key(k) < version_key(running)]
    if not older:
        return None
    same = [k for k in older if _flavor(k) == _flavor(running)]
    return max(same or older, key=version_key)


# ---- GRUB helpers --------------------------------------------------------------
@dataclass
class MenuEntry:
    title: str
    path: str  # what grub-reboot accepts, e.g. "Advanced options for Ubuntu>Ubuntu, with Linux 6.8.0-40-generic"


def parse_grub_entries(cfg_text: str) -> list[MenuEntry]:
    """Extract bootable menu entries (with their submenu path) from grub.cfg."""
    stack: list[tuple[str, str]] = []
    entries: list[MenuEntry] = []
    for line in cfg_text.splitlines():
        m = _MENU_RE.match(line)
        if m:
            kind, _, title = m.groups()
            if kind == "menuentry":
                entries.append(MenuEntry(title=title, path=">".join([t for k, t in stack if k == "submenu"] + [title])))
            if line.rstrip().endswith("{"):
                stack.append((kind, title))
            continue
        stripped = line.strip()
        if stripped.startswith("}") and stack:
            stack.pop()
        elif stripped.endswith("{") and not stripped.startswith("#"):
            stack.append(("other", ""))
    return entries


def find_menu_entry(entries: list[MenuEntry], version: str) -> Optional[MenuEntry]:
    pat = re.compile(r"(?<![\d.])" + re.escape(version) + r"(?![\d.])")
    cands = [e for e in entries if pat.search(e.title) and "recovery" not in e.title.lower()]
    return cands[0] if cands else None


def detect_bootloader() -> str:
    if any(Path(p).is_file() for p in GRUB_CFG_PATHS) and (have("grub-reboot") or have("grub2-reboot")):
        return "grub"
    efivars = Path("/sys/firmware/efi/efivars")
    if efivars.is_dir() and any(efivars.glob("LoaderInfo-*")):
        return "systemd-boot"
    return "unknown"


def grub_default_is_saved(path: str = "/etc/default/grub") -> bool:
    try:
        text = Path(path).read_text()
    except OSError:
        return False
    for line in reversed(text.splitlines()):
        m = re.match(r"^\s*GRUB_DEFAULT\s*=\s*[\"']?([^\"'#\s]*)", line)
        if m:
            return m.group(1) == "saved"
    return False


def _read_grub_cfg() -> Optional[str]:
    for p in GRUB_CFG_PATHS:
        try:
            return Path(p).read_text(errors="replace")
        except OSError:
            continue
    return None


def _grub_tool(*names: str) -> Optional[str]:
    for n in names:
        if have(n):
            return n
    return None


MANUAL_BOOT_HELP = ("To try the older kernel by hand: restart, hold Shift (BIOS) or press Esc (UEFI) at startup to open the GRUB menu, "
                    "choose 'Advanced options', and pick the older kernel. Your default boot choice stays unchanged.")


class ListKernels(Tool):
    name = "list_kernels"
    description = "List installed Linux kernels, the one running now, and the previous (older) one. Read-only."
    read_only = True
    parameters: dict[str, dict[str, Any]] = {}

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "List the installed kernels (nothing is changed)."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.env.os != "linux":
            raise ToolError("Kernel selection is only supported on Linux.")
        installed = installed_kernels()
        running = ctx.env.kernel
        prev = previous_kernel(installed, running)
        loader = detect_bootloader()
        saved = grub_default_is_saved() if loader == "grub" else None
        lines = [f"Running kernel: {running}", f"Installed: {', '.join(installed) or 'none found in /boot'}",
                 f"Previous kernel: {prev or 'none (no older kernel is installed)'}", f"Bootloader: {loader}"]
        if loader == "grub":
            lines.append("One-time boot ready: " + ("yes" if saved else "no (GRUB_DEFAULT is not 'saved')"))
        return ToolResult(ok=True, output="\n".join(lines), reversibility=Reversibility.FULL,
                          data={"running": running, "installed": installed, "previous": prev, "bootloader": loader,
                                "grub_default_saved": saved, "untrusted": False})


class RebootIntoKernelOnce(Tool):
    name = "boot_kernel_once"
    description = (
        "Prepare a ONE-TIME boot of another installed kernel (default: the previous one) without changing the default "
        "boot entry. With reboot=true it also restarts the computer; unsaved work is lost."
    )
    parameters = {
        "kernel_version": {"type": "string", "description": "Kernel release to boot once (e.g. '6.8.0-40-generic'). Default: the previous kernel."},
        "reboot": {"type": "boolean", "description": "Restart now (default false: only prepare the one-time boot)."},
    }

    def reversibility_for(self, args: dict[str, Any], ctx: Any = None) -> Reversibility:
        return Reversibility.NONE if args.get("reboot") else Reversibility.FULL

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        which = args.get("kernel_version") or "the previous kernel"
        base = f"Set the computer to start {which} one time only; the normal default is not changed."
        return base + (" Then restart the computer now." if args.get("reboot") else "")

    def undo_hint_for(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if args.get("reboot"):
            return "A restart cannot be undone; save your work first."
        return "Cancel with: sudo grub-editenv - unset next_entry"

    def _precheck(self, args: dict[str, Any], ctx: ToolContext) -> tuple[str, MenuEntry]:
        """Validate everything needed; returns (target_version, menu_entry). Raises ToolError."""
        if ctx.env.os != "linux":
            raise ToolError("Booting another kernel once is only supported on Linux.")
        installed = installed_kernels()
        running = ctx.env.kernel
        target = args.get("kernel_version") or previous_kernel(installed, running)
        if not target:
            raise ToolError("No older kernel is installed, so there is nothing to boot instead. " + MANUAL_BOOT_HELP)
        if target == running:
            raise ToolError(f"{target} is the kernel that is already running.")
        if target not in installed:
            raise ToolError(f"Kernel {target} is not installed. Installed kernels: {', '.join(installed) or 'none'}.")
        loader = detect_bootloader()
        if loader != "grub":
            raise ToolError(f"This computer does not use GRUB (detected: {loader}), so a one-time kernel boot cannot be set automatically. " + MANUAL_BOOT_HELP)
        if not grub_default_is_saved():
            raise ToolError(
                "GRUB would ignore a one-time boot request because GRUB_DEFAULT is not set to 'saved' in /etc/default/grub. "
                "Changing it is a bootloader change that Hasshr will not make silently. Either set it yourself "
                "(GRUB_DEFAULT=saved, then run update-grub) or use the manual method. " + MANUAL_BOOT_HELP)
        cfg = _read_grub_cfg()
        if cfg is None:
            raise ToolError("Could not read grub.cfg to find the boot menu entry. " + MANUAL_BOOT_HELP)
        entry = find_menu_entry(parse_grub_entries(cfg), target)
        if entry is None:
            raise ToolError(f"No GRUB menu entry was found for kernel {target}. " + MANUAL_BOOT_HELP)
        return target, entry

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        pv = Preview(explanation=self.explain(args, ctx), reversibility=self.reversibility_for(args, ctx),
                     undo_hint=self.undo_hint_for(args, ctx), needs_admin=True)
        try:
            target, entry = self._precheck(args, ctx)
            pv.detail = f"Kernel {target} via boot menu entry: {entry.path}"
        except ToolError as exc:
            pv.warnings.append(str(exc))
        if args.get("reboot"):
            pv.warnings.append("The computer will restart; save your work. After the restart run 'hasshr resume'.")
        return pv

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        target, entry = self._precheck(args, ctx)
        if args.get("reboot") and not ctx.extras.get("ready_to_reboot"):
            raise ToolError("Not restarting yet: the case state must be saved first so 'hasshr resume' can continue afterwards.")
        reboot_prog = _grub_tool("grub-reboot", "grub2-reboot")
        editenv = _grub_tool("grub-editenv", "grub2-editenv")
        if reboot_prog is None:
            raise ToolError("grub-reboot was not found.")
        ensure_admin(ctx)
        proc = subprocess.run(["sudo", "-n", reboot_prog, entry.path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise ToolError(f"grub-reboot failed: {proc.stderr.strip() or 'unknown error'}. Nothing was changed.")

        verified: Optional[bool] = None
        if editenv:
            chk = subprocess.run(["sudo", "-n", editenv, "list"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            if chk.returncode == 0:
                verified = f"next_entry={entry.path}" in chk.stdout
                if not verified:
                    subprocess.run(["sudo", "-n", editenv, "-", "unset", "next_entry"], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
                    raise ToolError("The one-time boot request could not be verified, so it was cancelled. " + MANUAL_BOOT_HELP)
        self._journal(ctx, f"{reboot_prog} '{entry.path}'", undo={"op": "bootonce", "editenv": editenv, "entry": entry.path, "kernel": target})

        message = f"Next start will use kernel {target} one time only (the default boot choice is unchanged)."
        if args.get("reboot"):
            message += " Restarting now."
            reboot_cmd = ["sudo", "-n", "systemctl", "reboot"] if have("systemctl") else ["sudo", "-n", "reboot"]
            subprocess.Popen(reboot_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ToolResult(ok=True, output=message, reversibility=self.reversibility_for(args, ctx), undo_hint=self.undo_hint_for(args, ctx),
                          data={"kernel": target, "menu_entry": entry.path, "verified": verified, "rebooting": bool(args.get("reboot")),
                                "default_changed": False, "untrusted": False})
