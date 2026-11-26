#!/usr/bin/env python3
"""Provision a Debian 13 (trixie) media server.

setup.sh is only a bootstrap that installs Python 3 and runs this file. Every
step below is idempotent, so the script can be re-run after installing new
software. The installer preseed (deb/media/preseed.cfg) already installs XFCE,
XRDP, SSH, fail2ban, VLC, Python and Arduino support; this script adds Chrome,
VSCodium, Jellyfin, uv and Tailscale and finishes the desktop, security and
shell configuration.
"""

from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

HOME = Path.home()
TARGET_USER = os.environ.get("USER") or getpass.getuser()
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
SUITE = "trixie"
KEYRINGS_DIR = Path("/usr/share/keyrings")
ETC_KEYRINGS_DIR = Path("/etc/apt/keyrings")

os.environ.setdefault("DEBIAN_FRONTEND", "noninteractive")

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_COLOR = sys.stdout.isatty()


class SetupError(RuntimeError):
    """A step failed in a way that should stop the setup."""


def _paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def log(message: str) -> None:
    print(f"{_paint('[+]', '0;32')} {message}", flush=True)


def warn(message: str) -> None:
    print(f"{_paint('[!]', '1;33')} {message}", flush=True)


def section(title: str) -> None:
    print(f"\n{'=' * 58}\n  {title}\n{'=' * 58}", flush=True)


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------


def run(
    argv: Sequence[str],
    *,
    sudo: bool = False,
    check: bool = True,
    input_data: str | bytes | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command, streaming its output unless ``quiet`` is set."""
    command = ["sudo", *argv] if sudo else list(argv)
    binary_input = isinstance(input_data, (bytes, bytearray))
    result = subprocess.run(
        command,
        input=input_data,
        cwd=cwd,
        env=env,
        text=not binary_input,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
    )
    if check and result.returncode != 0:
        raise SetupError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def capture(argv: Sequence[str], *, sudo: bool = False, check: bool = True) -> str:
    result = run(argv, sudo=sudo, check=check, quiet=True)
    return (result.stdout or "").strip()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def package_installed(name: str) -> bool:
    result = run(["dpkg-query", "-W", "-f=${Status}", name], check=False, quiet=True)
    return result.returncode == 0 and (result.stdout or "").strip() == "install ok installed"


def write_root_file(path: Path, content: str | bytes, mode: int = 0o644) -> None:
    data = content.encode() if isinstance(content, str) else content
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(data)
        temporary_path = handle.name
    try:
        run(
            ["install", "-D", "-o", "root", "-g", "root", "-m", f"{mode:04o}", temporary_path, str(path)],
            sudo=True,
        )
    finally:
        os.unlink(temporary_path)


def write_root_file_if_changed(path: Path, content: str | bytes, mode: int = 0o644) -> bool:
    """Write a root-owned file only when its content or mode differs."""
    data = content.encode() if isinstance(content, str) else content
    try:
        if path.read_bytes() == data and (path.stat().st_mode & 0o777) == mode:
            log(f"{path.name} is already up to date.")
            return False
    except OSError:
        pass
    write_root_file(path, data, mode)
    return True


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def download(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "optimumunknown-setup/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_to(url: str, destination: Path, timeout: int = 600) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "optimumunknown-setup/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def run_remote_script(url: str) -> None:
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as handle:
        script_path = Path(handle.name)
    try:
        download_to(url, script_path)
        run(["bash", str(script_path)])
    finally:
        script_path.unlink(missing_ok=True)


def optional(label: str, step: Callable[[], None]) -> None:
    try:
        step()
    except (SetupError, OSError, ValueError, KeyError) as error:
        warn(f"{label} was skipped: {error}")


# ---------------------------------------------------------------------------
# APT
# ---------------------------------------------------------------------------


class Apt:
    def update(self) -> None:
        run(["apt-get", "update"], sudo=True)

    def upgrade(self) -> None:
        run(["apt-get", "upgrade", "-y"], sudo=True)

    def install(self, packages: Sequence[str]) -> None:
        if not packages:
            return
        missing = [package for package in packages if not package_installed(package)]
        if not missing:
            log("All requested packages are already installed.")
            return
        run(["apt-get", "install", "-y", *missing], sudo=True)


APT = Apt()


@dataclass(frozen=True)
class AptRepository:
    name: str
    key_url: str
    keyring: Path
    source_path: Path
    source_text: str
    architectures: tuple[str, ...] = ()


REPOSITORIES = (
    AptRepository(
        name="Google Chrome",
        key_url="https://dl.google.com/linux/linux_signing_key.pub",
        keyring=ETC_KEYRINGS_DIR / "google-chrome.gpg",
        source_path=Path("/etc/apt/sources.list.d/google-chrome.list"),
        source_text=(
            "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] "
            "https://dl.google.com/linux/chrome/deb/ stable main"
        ),
        architectures=("amd64",),
    ),
    AptRepository(
        name="VSCodium",
        key_url="https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/raw/master/pub.gpg",
        keyring=KEYRINGS_DIR / "vscodium-archive-keyring.gpg",
        source_path=Path("/etc/apt/sources.list.d/vscodium.list"),
        source_text=(
            "deb [ signed-by=/usr/share/keyrings/vscodium-archive-keyring.gpg ] "
            "https://download.vscodium.com/debs vscodium main"
        ),
    ),
    AptRepository(
        name="Jellyfin",
        key_url="https://repo.jellyfin.org/jellyfin_team.gpg.key",
        keyring=ETC_KEYRINGS_DIR / "jellyfin.gpg",
        source_path=Path("/etc/apt/sources.list.d/jellyfin.sources"),
        source_text=(
            "Types: deb\n"
            "URIs: https://repo.jellyfin.org/debian\n"
            "Suites: {suite}\n"
            "Components: main\n"
            "Architectures: {arch}\n"
            "Signed-By: /etc/apt/keyrings/jellyfin.gpg"
        ),
        architectures=("amd64", "arm64"),
    ),
)

JELLYFIN_ARCHITECTURES = ("amd64", "arm64")


def _dearmor(key_data: bytes) -> bytes:
    result = subprocess.run(
        ["gpg", "--dearmor"],
        input=key_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout if result.returncode == 0 and result.stdout else key_data


def _nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def ensure_repository(repository: AptRepository) -> bool:
    changed = False
    if not _nonempty(repository.keyring):
        log(f"Adding the {repository.name} signing key...")
        write_root_file(repository.keyring, _dearmor(download(repository.key_url)))
        changed = True
    if not repository.source_path.exists():
        log(f"Adding the {repository.name} repository...")
        source_text = repository.source_text.format(arch=ARCH, suite=SUITE)
        write_root_file(repository.source_path, source_text.rstrip("\n") + "\n")
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Package selection
# ---------------------------------------------------------------------------


PACKAGE_GROUPS = (
    (
        "core utilities",
        ("curl", "wget", "ca-certificates", "gnupg", "lsb-release", "usbutils"),
    ),
    (
        "XFCE desktop and XRDP",
        ("xfce4", "xfce4-goodies", "xorgxrdp", "xrdp", "dbus-x11"),
    ),
    (
        "remote access and security",
        ("openssh-server", "fail2ban"),
    ),
    (
        "media apps",
        ("vlc",),
    ),
    (
        "Python",
        ("python3", "python3-pip", "python3-venv"),
    ),
    (
        "Arduino serial support",
        ("arduino", "picocom"),
    ),
)


# ---------------------------------------------------------------------------
# Base system
# ---------------------------------------------------------------------------


def ensure_normal_user() -> None:
    if os.geteuid() == 0:
        raise SetupError("run this script as your normal user, not as root")


def prepare_environment() -> None:
    for directory in (HOME / ".local/bin", HOME / ".cargo/bin"):
        os.environ["PATH"] = f"{directory}{os.pathsep}{os.environ['PATH']}"
        directory.mkdir(parents=True, exist_ok=True)


def install_bootstrap_tools() -> None:
    log("Installing bootstrap tools...")
    APT.install(["curl", "wget", "ca-certificates", "gnupg"])
    for directory in (KEYRINGS_DIR, ETC_KEYRINGS_DIR):
        run(["install", "-d", "-m", "0755", str(directory)], sudo=True)


def update_and_upgrade() -> None:
    log("Updating APT package lists and upgrading installed packages...")
    APT.update()
    APT.upgrade()


def add_repositories() -> None:
    changed = False
    for repository in REPOSITORIES:
        if repository.architectures and ARCH not in repository.architectures:
            continue
        changed |= ensure_repository(repository)
    if changed:
        APT.update()


def install_system_packages() -> None:
    for label, packages in PACKAGE_GROUPS:
        log(f"Installing {label}...")
        APT.install(packages)


# ---------------------------------------------------------------------------
# Third-party applications
# ---------------------------------------------------------------------------


def install_chrome() -> None:
    if ARCH != "amd64":
        warn(f"Google Chrome is not available for {ARCH}; skipping.")
        return
    log("Installing Google Chrome...")
    APT.install(["google-chrome-stable"])


def install_vscodium() -> None:
    if command_exists("codium"):
        log("VSCodium is already installed.")
        return
    log("Installing VSCodium...")
    APT.install(["codium"])


def install_jellyfin() -> None:
    if ARCH not in JELLYFIN_ARCHITECTURES:
        warn(f"Jellyfin is not available for {ARCH}; skipping.")
        return
    if package_installed("jellyfin"):
        log("Jellyfin is already installed.")
    else:
        log("Installing Jellyfin...")
        APT.install(["jellyfin"])
    run(["systemctl", "enable", "--now", "jellyfin"], sudo=True)


def install_uv() -> None:
    if command_exists("uv"):
        log("Astral uv is already installed.")
        return
    log("Installing Astral uv...")
    run_remote_script("https://astral.sh/uv/install.sh")


def install_tailscale() -> None:
    if command_exists("tailscale"):
        log("Tailscale is already installed.")
        return
    log("Installing Tailscale...")
    run_remote_script("https://tailscale.com/install.sh")


# ---------------------------------------------------------------------------
# XRDP / XFCE desktop
# ---------------------------------------------------------------------------


def configure_xrdp() -> None:
    log("Configuring the XRDP session for XFCE...")
    if capture(["getent", "group", "ssl-cert"], check=False):
        run(["usermod", "-aG", "ssl-cert", "xrdp"], sudo=True, check=False)

    changed = patch_startwm()
    run(["systemctl", "enable", "--now", "xrdp"], sudo=True)
    if changed:
        run(["systemctl", "restart", "xrdp"], sudo=True)


def patch_startwm() -> bool:
    startwm = Path("/etc/xrdp/startwm.sh")
    if not startwm.exists():
        return False
    text = capture(["cat", str(startwm)], sudo=True)
    changed = False
    if "startxfce4" not in text:
        log("Pointing XRDP at XFCE...")
        text = re.sub(r"^test -x /etc/X11/Xsession.*$", "# &", text, flags=re.MULTILINE)
        text = re.sub(r"^exec /bin/sh /etc/X11/Xsession.*$", "# &", text, flags=re.MULTILINE)
        text = text.rstrip("\n") + "\nstartxfce4\n"
        changed = True
    if "DBUS_SESSION_BUS_ADDRESS" not in text:
        text = re.sub(
            r"^(#!/bin/sh)\n",
            r"\1\nunset DBUS_SESSION_BUS_ADDRESS\nunset XDG_RUNTIME_DIR\n",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        changed = True
    if changed:
        write_root_file(startwm, text, 0o755)
    return changed


FAIL2BAN_JAIL = """[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd
"""


def configure_fail2ban() -> None:
    log("Configuring fail2ban to block SSH brute-force attempts...")
    if run(["systemctl", "enable", "--now", "ssh"], sudo=True, check=False).returncode:
        warn("Could not start ssh.service; fail2ban will protect it once it is running.")
    changed = write_root_file_if_changed(Path("/etc/fail2ban/jail.local"), FAIL2BAN_JAIL)
    run(["systemctl", "enable", "--now", "fail2ban"], sudo=True)
    if changed:
        run(["systemctl", "restart", "fail2ban"], sudo=True)

    status = run(["fail2ban-client", "status", "sshd"], sudo=True, check=False, quiet=True)
    for _ in range(5):
        if status.returncode == 0:
            break
        time.sleep(1)
        status = run(["fail2ban-client", "status", "sshd"], sudo=True, check=False, quiet=True)
    if status.returncode == 0:
        log("fail2ban is protecting SSH with the sshd jail.")
    else:
        warn("fail2ban is running, but the sshd jail did not start; check 'fail2ban-client status'.")


# ---------------------------------------------------------------------------
# GUI toggles, shell helpers
# ---------------------------------------------------------------------------


ENABLE_GUI = """#!/usr/bin/env bash
echo "Enabling local GUI on boot (setting graphical.target)..."
sudo systemctl set-default graphical.target
echo "Starting local display manager..."
sudo systemctl start display-manager 2>/dev/null || sudo systemctl start lightdm 2>/dev/null || sudo systemctl start gdm3 2>/dev/null || sudo systemctl start sddm 2>/dev/null || true
echo "Local GUI enabled. Default boot target is now graphical.target."
"""

DISABLE_GUI = """#!/usr/bin/env bash
echo "Disabling local GUI on boot (setting multi-user.target)..."
sudo systemctl set-default multi-user.target
echo "Stopping local display manager..."
sudo systemctl stop display-manager 2>/dev/null || sudo systemctl stop lightdm 2>/dev/null || sudo systemctl stop gdm3 2>/dev/null || sudo systemctl stop sddm 2>/dev/null || true
echo "Local GUI disabled. Default boot target is now multi-user.target (headless)."
"""


def install_gui_toggles() -> None:
    log("Installing 'enable-gui' and 'disable-gui' commands...")
    write_root_file_if_changed(Path("/usr/local/bin/enable-gui"), ENABLE_GUI, 0o755)
    write_root_file_if_changed(Path("/usr/local/bin/disable-gui"), DISABLE_GUI, 0o755)


def install_cheatsheet() -> None:
    log("Installing the 'h' cheat sheet command...")
    write_root_file_if_changed(Path("/usr/local/bin/h"), CHEAT_SHEET, 0o755)


CHEAT_SHEET = r"""#!/usr/bin/env bash
cat << 'CHEAT'
================================================================================
                    MEDIA SERVER TOOLS CHEAT SHEET
================================================================================
[Remote Access & System]
  ssh / scp           - OpenSSH remote shell and secure file copy
  xrdp                - RDP server for the XFCE desktop
  enable-gui          - Boot to graphical.target & start the display manager
  disable-gui         - Boot to multi-user.target (headless) & stop the display manager
  systemctl           - Manage services (status xrdp, restart fail2ban)
  journalctl          - Read the systemd journal (journalctl -u xrdp -e)
  h                   - Show this cheat sheet

[Editors]
  codium              - Open-source, telemetry-free VS Code editor

[Media]
  vlc                 - Media player (cvlc for the console)
  vlc --intf dummy    - Play or stream without opening a window
  jellyfin            - Media server; browse and stream at http://localhost:8096
  systemctl status jellyfin  - Show whether the Jellyfin server is running
  journalctl -u jellyfin     - Read the Jellyfin server log

[Python]
  python3 / pip3      - Python 3 interpreter and standard package manager
  python3 -m venv     - Create isolated Python virtual environments
  uv                  - Ultra-fast Python package installer & resolver
  uv venv             - Create a virtual environment with uv
  uv pip install      - Install packages with uv

[Arduino & Serial]
  arduino             - Arduino IDE with the built-in serial monitor
  picocom -b 115200 /dev/ttyACM0 - Minimal serial terminal for a connected board
  lsusb               - List USB devices
  dialout group       - Grants access to /dev/ttyUSB* and /dev/ttyACM*

[Web & Networking]
  google-chrome       - Google Chrome browser
  curl / wget         - Command-line HTTP and data transfer utilities
  tailscale           - Zero-config mesh VPN (status, up, ip)
  tailscale up        - Join the tailnet (prints a login URL)
  tailscale status    - Show peers and connection state
  ip addr / ss -ltn   - Inspect interfaces and listening ports

[Security]
  fail2ban-client status sshd          - Show the SSH jail and banned IPs
  fail2ban-client set sshd unbanip IP  - Unban an address
================================================================================
CHEAT
"""


# ---------------------------------------------------------------------------
# Users and groups
# ---------------------------------------------------------------------------


def add_user_to_groups() -> None:
    log(f"Adding {TARGET_USER} to the dialout group for Arduino serial access...")
    if capture(["getent", "group", "dialout"], check=False):
        run(["usermod", "-aG", "dialout", TARGET_USER], sudo=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def print_summary() -> None:
    section("Setup complete")
    warn("Log out and back in for the dialout group change to take effect.")
    warn("Run 'sudo tailscale up' to join your tailnet.")


def main() -> int:
    try:
        ensure_normal_user()
        prepare_environment()
    except SetupError as error:
        warn(str(error))
        return 1

    section("Debian media server setup")
    steps = (
        ("package lists", update_and_upgrade),
        ("bootstrap tools", install_bootstrap_tools),
        ("apt repositories", add_repositories),
        ("system packages", install_system_packages),
        ("Google Chrome", install_chrome),
        ("VSCodium", install_vscodium),
        ("Jellyfin", install_jellyfin),
        ("XRDP desktop", configure_xrdp),
        ("fail2ban SSH protection", configure_fail2ban),
        ("GUI toggles", install_gui_toggles),
        ("cheat sheet", install_cheatsheet),
        ("user groups", add_user_to_groups),
    )
    optional_steps = (
        ("Astral uv", install_uv),
        ("Tailscale", install_tailscale),
    )

    try:
        for label, step in steps:
            section(label)
            step()
        for label, step in optional_steps:
            section(label)
            optional(label, step)
    except SetupError as error:
        warn(str(error))
        return 1
    except KeyboardInterrupt:
        warn("Setup interrupted.")
        return 130

    print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
