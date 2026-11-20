#!/usr/bin/env python3
"""Provision a Debian 13 (trixie) headless development machine.

setup.sh is only a bootstrap that installs Python 3 and runs this file. Every
step below is idempotent, so the script can be re-run after installing new
software. Prompts are read from /dev/tty, which keeps `wget -qO- ... | bash`
working without swallowing the remaining script text.
"""

from __future__ import annotations

import getpass
import json
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
from typing import Callable, Iterator, Sequence

HOME = Path.home()
TARGET_USER = os.environ.get("USER") or getpass.getuser()
ARCH = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
SUITE = "trixie"
KEYRINGS_DIR = Path("/usr/share/keyrings")
ETC_KEYRINGS_DIR = Path("/etc/apt/keyrings")

os.environ.setdefault("DEBIAN_FRONTEND", "noninteractive")

NVM_DIR = Path(os.environ.get("NVM_DIR") or HOME / ".nvm")
if not (NVM_DIR / "nvm.sh").is_file():
    xdg_nvm = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "nvm"
    if (xdg_nvm / "nvm.sh").is_file():
        NVM_DIR = xdg_nvm

CRAFT_APPS = (
    ("photocraft", "storytold/photocraft"),
    ("filmcraft", "storytold/filmcraft"),
    ("vectorcraft", "storytold/vectorcraft"),
    ("soundcraft", "storytold/soundcraft"),
    ("anicraft", "storytold/anicraft"),
    ("pdfcraft", "storytold/pdfcraft"),
    ("craft-anything", "storytold/craft-anything"),
    ("artcraft-launcher", "storytold/craft-launcher"),
)

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


def installed_version(name: str) -> str:
    result = run(["dpkg-query", "-W", "-f=${Version}", name], check=False, quiet=True)
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def write_user_file(path: Path, content: str | bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode() if isinstance(content, str) else content
    path.write_bytes(data)
    if mode is not None:
        path.chmod(mode)


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


def latest_github_release(repository: str) -> dict:
    return json.loads(download(f"https://api.github.com/repos/{repository}/releases/latest"))


def release_asset(release: dict, suffix: str, repository: str) -> str:
    for asset in release.get("assets", []):
        if asset["name"].endswith(suffix):
            return asset["browser_download_url"]
    raise SetupError(f"no {suffix} asset in the latest {repository} release")


def install_deb(url: str, label: str) -> None:
    log(f"Installing {label}...")
    with tempfile.NamedTemporaryFile(suffix=".deb", delete=False) as handle:
        package_path = Path(handle.name)
    try:
        download_to(url, package_path)
        run(["apt-get", "install", "-y", str(package_path)], sudo=True)
    finally:
        package_path.unlink(missing_ok=True)


def _same_upstream_version(installed: str, version: str) -> bool:
    """Match a dpkg version such as ``0.23.1-1`` to a release tag such as ``v0.23.1``."""
    return bool(installed) and installed.rsplit("-", 1)[0] == version


def install_github_deb(repository: str, label: str) -> None:
    if ARCH not in {"amd64", "arm64"}:
        warn(f"{label} does not publish a Debian package for {ARCH}; skipping.")
        return
    suffix = "linux-x86_64.deb" if ARCH == "amd64" else "linux-aarch64.deb"
    release = latest_github_release(repository)
    version = str(release.get("tag_name") or "").lstrip("vV")
    current = installed_version(label)
    if _same_upstream_version(current, version):
        log(f"{label} {current} is already installed.")
        return
    install_deb(release_asset(release, suffix, repository), label)


def optional(label: str, step: Callable[[], None]) -> None:
    try:
        step()
    except (SetupError, OSError, ValueError, KeyError) as error:
        warn(f"{label} was skipped: {error}")


# ---------------------------------------------------------------------------
# Prompts (read from /dev/tty so piped execution still works)
# ---------------------------------------------------------------------------


class Prompter:
    def __init__(self) -> None:
        self._stream = None
        try:
            self._stream = open("/dev/tty", "r+")
        except OSError:
            self._stream = None

    @property
    def available(self) -> bool:
        return self._stream is not None

    def _ask(self, question: str) -> str:
        if self._stream is None:
            return ""
        self._stream.write(question)
        self._stream.flush()
        answer = self._stream.readline()
        return answer.strip()

    def confirm(self, question: str, default: bool = False) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        answer = self._ask(f"{question} {suffix} ")
        if not answer:
            return default
        return answer.lower().startswith("y")

    def text(self, question: str, default: str = "") -> str:
        answer = self._ask(f"{question} ")
        return answer or default


PROMPT = Prompter()


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
    source_line: str
    architectures: tuple[str, ...] = ()


REPOSITORIES = (
    AptRepository(
        name="VeraCrypt",
        key_url="https://download.opensuse.org/repositories/home:unit193:/veracrypt/Debian_13/Release.key",
        keyring=KEYRINGS_DIR / "home_unit193_veracrypt.gpg",
        source_path=Path("/etc/apt/sources.list.d/home:unit193:veracrypt.list"),
        source_line=(
            "deb [signed-by=/usr/share/keyrings/home_unit193_veracrypt.gpg] "
            "http://download.opensuse.org/repositories/home:/unit193:/veracrypt/Debian_13/ /"
        ),
    ),
    AptRepository(
        name="Microsoft Visual Studio Code",
        key_url="https://packages.microsoft.com/keys/microsoft.asc",
        keyring=KEYRINGS_DIR / "microsoft-archive-keyring.gpg",
        source_path=Path("/etc/apt/sources.list.d/vscode.list"),
        source_line=(
            "deb [arch={arch} signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] "
            "https://packages.microsoft.com/repos/code stable main"
        ),
    ),
    AptRepository(
        name="Google Chrome",
        key_url="https://dl.google.com/linux/linux_signing_key.pub",
        keyring=ETC_KEYRINGS_DIR / "google-chrome.gpg",
        source_path=Path("/etc/apt/sources.list.d/google-chrome.list"),
        source_line=(
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
        source_line=(
            "deb [ signed-by=/usr/share/keyrings/vscodium-archive-keyring.gpg ] "
            "https://download.vscodium.com/debs vscodium main"
        ),
    ),
    AptRepository(
        name="Docker",
        key_url="https://download.docker.com/linux/debian/gpg",
        keyring=ETC_KEYRINGS_DIR / "docker.gpg",
        source_path=Path("/etc/apt/sources.list.d/docker.list"),
        source_line=(
            "deb [arch={arch} signed-by=/etc/apt/keyrings/docker.gpg] "
            "https://download.docker.com/linux/debian {suite} stable"
        ),
    ),
    AptRepository(
        name="Google Cloud",
        key_url="https://packages.cloud.google.com/apt/doc/apt-key.gpg",
        keyring=KEYRINGS_DIR / "cloud.google.gpg",
        source_path=Path("/etc/apt/sources.list.d/google-cloud-sdk.list"),
        source_line=(
            "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] "
            "https://packages.cloud.google.com/apt cloud-sdk main"
        ),
    ),
    AptRepository(
        name="Cloudflare",
        key_url="https://pkg.cloudflare.com/cloudflare-main.gpg",
        keyring=KEYRINGS_DIR / "cloudflare-main.gpg",
        source_path=Path("/etc/apt/sources.list.d/cloudflared.list"),
        source_line=(
            "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] "
            "https://pkg.cloudflare.com/cloudflared any main"
        ),
    ),
)


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
        source_line = repository.source_line.format(arch=ARCH, suite=SUITE)
        write_root_file(repository.source_path, source_line + "\n")
        changed = True
    return changed


# ---------------------------------------------------------------------------
# Package selection
# ---------------------------------------------------------------------------


def _hardware_packages() -> tuple[str, ...]:
    packages = ["firmware-iwlwifi", "firmware-sof-signed", f"linux-headers-{ARCH}"]
    if ARCH == "amd64":
        packages += ["intel-microcode", "firmware-intel-graphics"]
    return tuple(packages)


PACKAGE_GROUPS = (
    (
        "core utilities",
        (
            "curl", "wget", "ca-certificates", "lsb-release", "git", "gh", "code",
            "ntfs-3g", "chromium", "flatpak", "pipx", "openssl", "ufw", "jq", "fzf",
            "ripgrep", "btop", "tmux", "bat", "ncdu", "lsof", "cron", "usbutils",
            "pciutils", "dmidecode",
        ),
    ),
    (
        "XFCE desktop and XRDP",
        (
            "xfce4", "xfce4-goodies", "xfce4-indicator-plugin", "xorgxrdp", "xrdp",
            "dbus-x11", "zram-tools", "remmina",
        ),
    ),
    (
        "remote access and networking",
        (
            "openssh-server", "fail2ban", "netcat-traditional", "traceroute",
            "bind9-dnsutils", "inetutils-telnet",
        ),
    ),
    (
        "compilers and Python",
        (
            "build-essential", "gcc", "g++", "gdb", "cmake", "python3", "python3-pip",
            "python3-venv", "python3-opencv", "libopencv-dev", "dpkg-dev", "autoconf",
            "libtool", "libltdl-dev",
        ),
    ),
    (
        "media libraries and apps",
        (
            "libavcodec-dev", "libavformat-dev", "libavutil-dev", "libswscale-dev",
            "libgl1-mesa-dev", "libglu1-mesa-dev", "libx11-dev", "libpulse-dev",
            "ffmpeg", "vlc", "handbrake",
        ),
    ),
    (
        "encryption and security",
        (
            "keepassxc", "encfs", "veracrypt", "fscrypt", "libpam-fscrypt", "tpm2-tools",
            "dracut", "cryptsetup", "systemd-cryptsetup",
        ),
    ),
    (
        "virtualization and chroots",
        (
            "virt-manager", "qemu-system-x86", "libvirt-daemon-system", "lvm2",
            "debootstrap", "schroot",
        ),
    ),
    ("disk and file tools", ("qdirstat", "rclone")),
    ("terminal toys", ("hollywood", "cmatrix")),
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


def configure_dracut() -> None:
    log("Configuring Dracut for LUKS2 TPM unlocking...")
    run(["install", "-d", "-m", "0755", "/etc/dracut.conf.d"], sudo=True)
    write_root_file_if_changed(Path("/etc/dracut.conf.d/20-luks-tpm.conf"), DRACUT_TPM_CONF)


DRACUT_TPM_CONF = """hostonly="yes"
hostonly_cmdline="yes"
install_items+=" /etc/crypttab "
add_dracutmodules+=" systemd-cryptsetup crypt lvm tpm2-tss "
omit_dracutmodules+=" console-setup "
"""


def install_hardware_support() -> None:
    log("Installing firmware, microcode and kernel headers...")
    APT.install(_hardware_packages())


def flatpak_installed(app_id: str) -> bool:
    result = run(["flatpak", "info", "--system", app_id], check=False, quiet=True)
    return result.returncode == 0


def install_flatpak_apps() -> None:
    app_id = "com.github.PintaProject.Pinta"
    if flatpak_installed(app_id):
        log("Pinta is already installed.")
        return
    log("Installing Pinta from Flathub...")
    run(
        ["flatpak", "remote-add", "--system", "--if-not-exists", "flathub",
         "https://flathub.org/repo/flathub.flatpakrepo"],
        sudo=True,
    )
    run(["flatpak", "install", "--system", "-y", "flathub", app_id], sudo=True)


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


# ---------------------------------------------------------------------------
# Third-party applications
# ---------------------------------------------------------------------------


def install_craft_apps() -> None:
    failures = []
    for app, repository in CRAFT_APPS:
        try:
            install_github_deb(repository, app)
        except (SetupError, OSError, ValueError, KeyError) as error:
            failures.append(app)
            warn(f"Could not install {app}: {error}")
    if failures:
        warn(f"Craft apps not installed: {', '.join(failures)}")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")


# (directory name, file name) -> (nested section or None, key, value)
CRAFT_JSON_SETTINGS = {
    ("photocraft", "preferences.json"): ("interface", "uiScale", "200"),
    ("filmcraft", "preferences.json"): ("appearance", "uiScale", "200"),
    ("vectorcraft", "ui.json"): ("engine_prefs", "uiScaling", 2),
    ("soundcraft", "ui.json"): (None, "ui_scale", 2),
    ("anicraft", "prefs.json"): (None, "ui_scale", 2),
}
CRAFT_EGUI_DIRS = {"pdfcraft", "ai.storyteller.artcraft-launcher", "ai.storyteller.craft-anything"}
CRAFT_PREFERENCE_NAMES = {"preferences.json", "ui.json", "prefs.json", "app.ron"}
EGUI_ZOOM_FACTOR = re.compile(r"(zoom_factor\s*:\s*)(?:\d+(?:\.\d*)?|\.\d+)")


def _replace_file(path: Path, content: str | bytes) -> None:
    """Atomically replace a file, preserving its permissions."""
    data = content.encode() if isinstance(content, str) else content
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        temporary.chmod(path.stat().st_mode)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _launcher_app_homes(launcher_apps: Path) -> list[Path]:
    """Match `find -mindepth 2 -maxdepth 2 -type d -name home`."""
    homes = []
    for directory, subdirectories, _ in os.walk(launcher_apps):
        depth = len(Path(directory).relative_to(launcher_apps).parts)
        if depth >= 2:
            subdirectories[:] = []
        if depth == 2 and Path(directory).name == "home":
            homes.append(Path(directory))
    return homes


def _craft_search_roots() -> list[Path]:
    data_home = _data_home()
    roots = [
        _config_home(),
        HOME / ".var" / "app",
        data_home / "filmcraft",
        data_home / "ai.storyteller.artcraft-launcher",
        data_home / "ai.storyteller.craft-anything",
    ]
    for app_home in _launcher_app_homes(data_home / "artcraft-launcher" / "apps"):
        roots.extend((app_home / ".config", app_home / ".local" / "share"))

    unique = []
    seen = set()
    for root in roots:
        if not root.is_dir() or str(root) in seen:
            continue
        seen.add(str(root))
        unique.append(root)
    return unique


def _craft_preference_files(roots: Sequence[Path]) -> Iterator[Path]:
    for root in roots:
        for directory, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower() in CRAFT_PREFERENCE_NAMES:
                    yield Path(directory) / filename


def _craft_setting(path: Path) -> tuple[str | None, str, object] | None:
    return CRAFT_JSON_SETTINGS.get((path.parent.name.lower(), path.name.lower()))


def _is_egui_preferences(path: Path) -> bool:
    return path.name.lower() == "app.ron" and path.parent.name.lower() in CRAFT_EGUI_DIRS


def _update_craft_json(path: Path, section: str | None, key: str, value: object) -> bool:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SetupError(f"not a valid JSON object ({error.msg})") from error
    if not isinstance(data, dict):
        raise SetupError("not a JSON object")
    target = data
    if section is not None:
        nested = data.get(section)
        if not isinstance(nested, dict):
            nested = {}
        data[section] = nested
        target = nested
    if target.get(key) == value:
        return False
    target[key] = value
    _replace_file(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True


def _update_egui_zoom(path: Path) -> bool:
    text = path.read_bytes().decode("utf-8")
    updated, count = EGUI_ZOOM_FACTOR.subn(lambda match: match.group(1) + "2.0", text)
    if count != 1:
        raise SetupError(f"expected one saved egui zoom factor, found {count}")
    if updated == text:
        return False
    _replace_file(path, updated.encode("utf-8"))
    return True


def configure_craft_scaling() -> None:
    log("Setting Craft app UI scaling to 200%...")
    found = 0
    updated = 0
    unchanged = 0
    failures = 0
    seen: set[Path] = set()
    for path in _craft_preference_files(_craft_search_roots()):
        setting = _craft_setting(path)
        egui = _is_egui_preferences(path)
        if setting is None and not egui:
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        found += 1
        try:
            if egui:
                changed = _update_egui_zoom(resolved)
            else:
                section, key, value = setting
                changed = _update_craft_json(resolved, section, key, value)
        except (SetupError, OSError) as error:
            failures += 1
            warn(f"Could not update {resolved}: {error}")
            continue
        if changed:
            updated += 1
            log(f"Set UI scale to 200%: {resolved}")
        else:
            unchanged += 1

    if not found:
        warn("No supported Craft app scale settings files were found.")
    if failures:
        warn(f"{failures} Craft app preference file(s) were left unchanged.")
    if updated:
        log(f"Updated {updated} Craft app preference file(s).")
    if unchanged:
        log(f"{unchanged} Craft app preference file(s) were already at 200%.")


def install_dropbox() -> None:
    if platform.machine() != "x86_64":
        warn(f"Dropbox does not publish a daemon for {ARCH}; skipping.")
        return
    dropbox_daemon = HOME / ".dropbox-dist" / "dropboxd"
    if not dropbox_daemon.exists():
        log("Installing Dropbox daemon...")
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
            archive = Path(handle.name)
        try:
            download_to("https://www.dropbox.com/download?plat=lnx.x86_64", archive)
            run(["tar", "-xzf", str(archive), "-C", str(HOME)])
        finally:
            archive.unlink(missing_ok=True)

    cli = HOME / ".local" / "bin" / "dropbox"
    if not cli.exists():
        write_user_file(cli, download("https://www.dropbox.com/download?dl=packages/dropbox.py"), 0o755)

    service = HOME / ".config" / "systemd" / "user" / "dropbox.service"
    write_user_file(service, DROPBOX_SERVICE, 0o644)
    run(["systemctl", "--user", "daemon-reload"], check=False)
    if run(["systemctl", "--user", "enable", "--now", "dropbox.service"], check=False).returncode:
        warn("Could not start the Dropbox user service; it will start at next login.")
    log("Dropbox is running. Inspect its log with: journalctl --user -u dropbox -f")


DROPBOX_SERVICE = f"""[Unit]
Description=Dropbox daemon
After=network-online.target

[Service]
ExecStart={HOME}/.dropbox-dist/dropboxd
Restart=on-failure

[Install]
WantedBy=default.target
"""


def install_mitmproxy() -> None:
    if command_exists("mitmproxy"):
        log("mitmproxy is already installed.")
        return
    log("Installing mitmproxy with pipx...")
    run(["pipx", "install", "mitmproxy"])


def install_antigravity_ide() -> None:
    if ARCH != "amd64":
        warn(f"Google Antigravity IDE is not available for {ARCH}; skipping.")
        return
    install_dir = Path("/opt/Antigravity IDE")
    log("Installing Google Antigravity IDE...")
    if not (install_dir / "antigravity-ide").exists():
        url = (
            "https://edgedl.me.gvt1.com/edgedl/release2/j0qc3/antigravity/stable/"
            "2.6.0-5526304964083712/linux-x64/Antigravity%20IDE.tar.gz"
        )
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
            archive = Path(handle.name)
        try:
            download_to(url, archive)
            run(["tar", "-xzf", str(archive), "-C", "/opt"], sudo=True)
        finally:
            archive.unlink(missing_ok=True)
    run(["ln", "-sfn", str(install_dir / "antigravity-ide"), "/usr/local/bin/antigravity-ide"], sudo=True)
    write_root_file_if_changed(Path("/usr/share/applications/antigravity-ide.desktop"), ANTIGRAVITY_DESKTOP)


def install_antigravity_cli() -> None:
    if command_exists("agy"):
        log("Google Antigravity CLI is already installed.")
        return
    log("Installing Google Antigravity CLI...")
    run_remote_script("https://antigravity.google/cli/install.sh")


def install_antigravity() -> None:
    install_antigravity_ide()
    install_antigravity_cli()


ANTIGRAVITY_DESKTOP = """[Desktop Entry]
Name=Google Antigravity IDE
Comment=Google's agentic development environment
Exec=/usr/local/bin/antigravity-ide %U
Terminal=false
Type=Application
Categories=Development;IDE;
"""


def install_kiri() -> None:
    if command_exists("kiri"):
        log("Kiri is already installed.")
        return
    log("Installing Kiri...")
    run_remote_script("https://runkiri.com/install.sh")


def install_fresh() -> None:
    if command_exists("fresh"):
        log("Fresh is already installed.")
        return
    log("Installing Fresh...")
    run_remote_script(
        "https://raw.githubusercontent.com/sinelaw/fresh/refs/heads/master/scripts/install.sh"
    )


def install_no_more_secrets() -> None:
    binaries = (Path("/usr/local/bin/nms"), Path("/usr/local/bin/sneakers"))
    if all(binary.exists() for binary in binaries):
        log("no-more-secrets is already installed.")
        return
    log("Building no-more-secrets (nms and sneakers)...")
    workdir = Path(tempfile.mkdtemp())
    try:
        project = workdir / "no-more-secrets"
        run(["git", "clone", "--depth", "1", "https://github.com/bartobri/no-more-secrets.git", str(project)])
        run(["make", "nms"], cwd=project)
        run(["make", "sneakers"], cwd=project)
        run(["make", "install"], cwd=project, sudo=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Node.js, uv, containers and CLIs
# ---------------------------------------------------------------------------


def _nvm(arguments: str, *, check: bool = True, quiet: bool = True) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["NVM_DIR"] = str(NVM_DIR)
    command = f'. "{NVM_DIR}/nvm.sh" && nvm {arguments}'
    result = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        text=True,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
    )
    if check and result.returncode != 0:
        raise SetupError(f"nvm {arguments} failed")
    return result


def _nvm_has_node() -> bool:
    output = _nvm("ls --no-colors", check=False).stdout or ""
    return re.search(r"v\d+", output) is not None


def install_node() -> None:
    if not (NVM_DIR / "nvm.sh").is_file():
        log("Installing NVM (Node Version Manager)...")
        if NVM_DIR.exists():
            shutil.rmtree(NVM_DIR)
        script = download("https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh").decode()
        environment = os.environ.copy()
        environment["NVM_DIR"] = str(NVM_DIR)
        result = subprocess.run(["bash"], input=script, text=True, env=environment)
        if result.returncode != 0:
            raise SetupError("the NVM installer failed")

    if not _nvm_has_node():
        log("Installing Node.js LTS...")
        _nvm("install --lts", quiet=False)
    _nvm("use --lts", check=False)
    _nvm("alias default 'lts/*'", check=False)

    node_path = (_nvm("which current", check=False).stdout or "").strip()
    if not node_path:
        node_path = (_nvm("which default", check=False).stdout or "").strip()
    if not node_path:
        raise SetupError("could not locate the Node.js binary after installing NVM")
    node_bin_dir = Path(node_path).parent
    os.environ["PATH"] = f"{node_bin_dir}{os.pathsep}{os.environ['PATH']}"
    for name in ("node", "npm", "npx"):
        run(["ln", "-sfn", str(node_bin_dir / name), f"/usr/local/bin/{name}"], sudo=True, check=False)
    log(f"Node.js {Path(node_path).name} is configured ({node_path}).")


NPM_GLOBALS = (("cf", "cf"), ("freebuff", "freebuff"), ("clixad", "clixad"))


def npm_install_globals() -> None:
    if not command_exists("npm"):
        warn("npm is not available; skipping global npm packages.")
        return
    for binary, package in NPM_GLOBALS:
        if command_exists(binary):
            log(f"{binary} is already installed.")
            continue
        try:
            log(f"Installing {package} globally with npm...")
            run(["npm", "install", "-g", package])
        except SetupError as error:
            warn(f"Could not install {package}: {error}")


def install_uv() -> None:
    if command_exists("uv"):
        log("Astral uv is already installed.")
        return
    log("Installing Astral uv...")
    run_remote_script("https://astral.sh/uv/install.sh")


def install_docker() -> None:
    if not command_exists("docker"):
        log("Installing Docker & Docker Compose...")
        APT.install(
            ["docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"]
        )
    else:
        log("Docker is already installed.")


def install_tailscale() -> None:
    if command_exists("tailscale"):
        log("Tailscale is already installed.")
        return
    log("Installing Tailscale...")
    run_remote_script("https://tailscale.com/install.sh")


def install_gcloud() -> None:
    if command_exists("gcloud"):
        log("Google Cloud CLI is already installed.")
        return
    log("Installing Google Cloud CLI...")
    APT.install(["google-cloud-cli"])


def install_cloudflare() -> None:
    if command_exists("cloudflared"):
        log("Cloudflared is already installed.")
        return
    log("Installing Cloudflared...")
    APT.install(["cloudflared"])


# ---------------------------------------------------------------------------
# XRDP / XFCE desktop
# ---------------------------------------------------------------------------


def _xfce_config_dir() -> Path:
    return _config_home() / "xfce4" / "xfconf" / "xfce-perchannel-xml"


XSETTINGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Gdk" type="empty">
    <property name="WindowScalingFactor" type="int" value="2"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="CursorThemeSize" type="int" value="48"/>
  </property>
</channel>
"""

XFWM4_XML = """<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="Default-xhdpi"/>
  </property>
</channel>
"""

XSESSION = f"""#!/usr/bin/env bash

# HiDPI environment variables for GTK / Qt in RDP sessions
export GDK_SCALE=2
export GDK_DPI_SCALE=0.5
export QT_SCALE_FACTOR=2
export XCURSOR_SIZE=48

# Background hook to enforce xfconf 2x scaling once xfsettingsd is running
(
    for i in {{1..20}}; do
        if xfconf-query -c xsettings -p /Gdk/WindowScalingFactor >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    xfconf-query -c xsettings -p /Gdk/WindowScalingFactor -s 2 --create -t int 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/theme -s Default-xhdpi --create -t string 2>/dev/null || true
    xfconf-query -c xsettings -p /Gtk/CursorThemeSize -s 48 --create -t int 2>/dev/null || true
) &

exec xfce4-session
"""

PROFILE_MARKER = "# HiDPI scaling for Qt apps (managed by setup.py)"


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    write_user_file(path, content)
    return True


def _set_xfce_property(path: Path, name: str, value: str, kind: str = "int") -> bool:
    if not path.exists():
        return False
    pattern = re.compile(
        rf'<property name="{re.escape(name)}" type="{re.escape(kind)}" value="[^"]*"\s*/>'
    )
    replacement = f'<property name="{name}" type="{kind}" value="{value}"/>'
    text = path.read_text()
    if not pattern.search(text):
        return False
    updated = pattern.sub(replacement, text, count=1)
    if updated == text:
        return False
    path.write_text(updated)
    return True


def configure_xfce_scaling_files() -> bool:
    log("Checking XFCE HiDPI scaling settings...")
    xfconf = _xfce_config_dir()
    xfconf.mkdir(parents=True, exist_ok=True)
    xsettings = xfconf / "xsettings.xml"
    xfwm4 = xfconf / "xfwm4.xml"
    changed = _write_if_missing(xsettings, XSETTINGS_XML)
    changed |= _write_if_missing(xfwm4, XFWM4_XML)
    changed |= _set_xfce_property(xsettings, "WindowScalingFactor", "2")
    changed |= _set_xfce_property(xsettings, "CursorThemeSize", "48")
    changed |= _set_xfce_property(xfwm4, "theme", "Default-xhdpi", kind="string")
    return changed


def write_xsession() -> bool:
    path = HOME / ".xsession"
    if path.exists() and path.read_text() == XSESSION and (path.stat().st_mode & 0o777) == 0o755:
        log("~/.xsession is already up to date.")
        return False
    log("Writing ~/.xsession...")
    write_user_file(path, XSESSION, 0o755)
    return True


def extend_profile() -> None:
    profile = HOME / ".profile"
    text = profile.read_text() if profile.exists() else ""
    if "managed by setup.sh" in text or "managed by setup.py" in text:
        return
    profile.parent.mkdir(parents=True, exist_ok=True)
    with profile.open("a") as handle:
        handle.write(f"\n{PROFILE_MARKER}\nexport QT_SCALE_FACTOR=2\n")


def apply_live_xfce_scaling() -> None:
    if not os.environ.get("DISPLAY") or not command_exists("xfconf-query"):
        return
    for channel, prop, value, kind in (
        ("xsettings", "/Gdk/WindowScalingFactor", "2", "int"),
        ("xfwm4", "/general/theme", "Default-xhdpi", "string"),
        ("xsettings", "/Gtk/CursorThemeSize", "48", "int"),
    ):
        run(
            ["xfconf-query", "-c", channel, "-p", prop, "-s", value, "--create", "-t", kind],
            check=False,
            quiet=True,
        )


def configure_xrdp() -> None:
    log("Configuring the XRDP session for XFCE...")
    if capture(["getent", "group", "ssl-cert"], check=False):
        run(["usermod", "-aG", "ssl-cert", "xrdp"], sudo=True, check=False)

    changed = configure_xfce_scaling_files()
    changed |= write_xsession()
    extend_profile()
    changed |= patch_startwm()
    apply_live_xfce_scaling()
    run(["systemctl", "enable", "--now", "xrdp"], sudo=True)
    if changed:
        run(["systemctl", "restart", "xrdp"], sudo=True)


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


# ---------------------------------------------------------------------------
# GUI toggles, polkit, shell helpers
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

POLKIT_RULE = """polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.color-manager.create-device" ||
         action.id == "org.freedesktop.color-manager.create-profile" ||
         action.id == "org.freedesktop.packagekit.system-sources-refresh") &&
        subject.isInGroup("sudo")) {
        return polkit.Result.YES;
    }
});
"""


def install_gui_toggles() -> None:
    log("Installing 'enable-gui' and 'disable-gui' commands...")
    write_root_file_if_changed(Path("/usr/local/bin/enable-gui"), ENABLE_GUI, 0o755)
    write_root_file_if_changed(Path("/usr/local/bin/disable-gui"), DISABLE_GUI, 0o755)


def install_polkit_rule() -> None:
    log("Silencing Polkit color-manager popups...")
    write_root_file_if_changed(Path("/etc/polkit-1/rules.d/02-allow-colord.rules"), POLKIT_RULE)


def _append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text() if path.exists() else ""
    if marker in text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + block)


def remove_legacy_h_function(path: Path) -> None:
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    kept = []
    skipping = False
    for line in lines:
        if "Terminal Tool Cheat Sheet Shortcut" in line:
            skipping = True
            continue
        if skipping:
            if line.strip() == "}":
                skipping = False
            continue
        kept.append(line)
    path.write_text("\n".join(kept) + "\n")


def configure_shell() -> None:
    log("Configuring shell aliases and NVM...")
    bashrc = HOME / ".bashrc"
    _append_once(bashrc, "alias bat='batcat'", "\nalias bat='batcat'\n")
    remove_legacy_h_function(bashrc)
    nvm_block = f"""
export NVM_DIR="{NVM_DIR}"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \\. "$NVM_DIR/bash_completion"
"""
    _append_once(bashrc, "NVM_DIR", nvm_block)


def install_cheatsheet() -> None:
    log("Installing the 'h' cheat sheet command...")
    write_root_file_if_changed(Path("/usr/local/bin/h"), CHEAT_SHEET, 0o755)


CHEAT_SHEET = r"""#!/usr/bin/env bash
cat << 'CHEAT'
================================================================================
                      INSTALLED DEV TOOLS CHEAT SHEET
================================================================================
[Development & Editors]
  codium              - Open-source, telemetry-free VS Code editor
  code                - Microsoft Visual Studio Code
  nvm                 - Node Version Manager (switch Node versions dynamically)
  node / npm / npx    - Node.js runtime, package manager & package runner
  gh                  - GitHub command-line client
  git                 - Version control (clone, commit, branch, merge)
  antigravity-ide     - Google Antigravity standalone IDE
  agy                 - Google Antigravity command-line agent
  freebuff / clixad   - AI coding CLIs
  fresh               - Terminal text editor with mouse support
  uv                  - Ultra-fast Python package installer & resolver
  pipx                - Install Python applications in isolated environments
  python3 / pip3      - Python 3 interpreter and standard package manager
  python3 -m venv     - Create isolated Python virtual environments
  gcc / g++           - C and C++ compilers
  make                - Build automation from Makefiles
  gdb                 - GNU source-level debugger
  cmake               - Cross-platform build system generator
  libopencv-dev       - OpenCV C++ headers & Python module
  dpkg-dev / autoconf - Debian packaging and GNU build tooling
  libtool             - Shared-library build helper
  schroot/debootstrap - Chroot environments and Debian bootstrapping

[Creative Apps]
  photocraft          - Native image editor
  filmcraft           - Native video editor
  vectorcraft         - Native vector illustration app
  soundcraft          - Native digital audio workstation
  anicraft            - Native 2D animation studio
  pdfcraft            - PDF workbench
  craft-anything      - Software analysis and reverse-engineering workbench
  artcraft-launcher   - Installer/updater for the Craft app suite
  pinta               - Drawing and image editing app (Flathub)
  flatpak             - Install and manage Flatpak apps (Pinta)

[Containerization & Cloud]
  docker / compose    - Container management platform and multi-container orchestrator
  kiri                - Tiny, fast Docker-compatible container runtime
  tailscale           - Zero-config mesh VPN based on WireGuard
  gcloud              - Google Cloud Platform command-line interface
  cf                  - Cloudflare API command-line tool
  cloudflared         - Cloudflare Tunnel daemon for exposing local ports securely

[Virtualization & Remote Access]
  virt-manager        - Graphical manager for KVM virtual machines
  qemu-system-x86     - Machine emulator and virtualizer
  virsh / libvirt     - Manage KVM/QEMU domains and virtual networks
  remmina             - Remote desktop client (RDP, VNC, SSH, SFTP)
  ssh / scp           - OpenSSH remote shell and secure file copy
  xrdp                - RDP server for the XFCE desktop

[Encryption & Security]
  cryptsetup          - Create, open and manage LUKS encrypted volumes
  dracut              - Build initramfs images for LUKS/TPM boot
  systemd-cryptenroll - Enroll TPM 2.0 keys for LUKS unlocking
  fscrypt             - Linux kernel ext4 folder encryption tool
  veracrypt           - Cross-platform file/partition encryption container manager
  encfs               - FUSE-based user-space encrypted filesystem
  keepassxc           - Offline encrypted password manager (.kdbx)
  keepassxc-cli       - Command-line access to KeePassXC databases
  rclone              - Sync & encrypt files to cloud storage providers
  tpm2-tools          - Hardware TPM 2.0 configuration utilities
  mitmproxy           - Isolated HTTP(S) proxy for authorized traffic debugging
  fail2ban-client     - Inspect jails and ban/unban IPs (status sshd)
  ufw                 - Uncomplicated Firewall command-line manager
  openssl             - TLS certificates, keys, digests and encryption

[Terminal Utilities & Productivity]
  curl / wget         - Command-line HTTP and data transfer utilities
  btop                - Modern, interactive terminal resource & process monitor
  tmux                - Terminal multiplexer for persistent sessions
  fzf                 - Interactive fuzzy finder (Ctrl+R / Ctrl+T)
  ripgrep (rg)        - Ultra-fast recursive line-matching search tool
  bat (batcat)        - Cat clone with syntax highlighting and Git integration
  jq                  - Command-line JSON processor and filter
  lsof                - List open files and the processes using them
  crontab             - Schedule recurring jobs with cron
  nc / traceroute     - Network probing utilities
  dig / nslookup      - DNS lookup tools (bind9-dnsutils)
  telnet              - Telnet client
  lsusb               - List USB devices (usbutils)
  lspci               - List PCI devices (pciutils)
  dmidecode           - Read hardware/DMI information (sudo)
  lsb_release         - Show distribution and release information

[Filesystems & Disk Usage]
  ntfs-3g             - Read and write NTFS filesystems
  lvm                 - Manage LVM physical and logical volumes
  qdirstat            - Graphical disk usage visualizer
  ncdu                - Interactive terminal disk usage analyzer

[Media & Video Tools]
  ffmpeg              - Command-line audio/video converter and video processor
  vlc                 - Cross-platform media player
  handbrake           - Video converter (GUI and HandBrakeCLI)

[Terminal Fun]
  hollywood           - Fill the console with Hollywood technobabble
  cmatrix             - Matrix-style scrolling terminal effect
  nms                 - Recreate the movie "Sneakers" data-decryption effect
  sneakers            - Full-screen "Sneakers" decryption effect

[Remote Access & System]
  dropbox             - Dropbox CLI (for example: dropbox status)
  h                   - Show this cheat sheet
  xfce4-indicator-plugin - Application indicators for the XFCE panel
  enable-gui          - Switch to graphical.target & start local display manager
  disable-gui         - Switch to multi-user.target (headless) & stop display manager
================================================================================
CHEAT
"""


# ---------------------------------------------------------------------------
# Users and groups
# ---------------------------------------------------------------------------


def add_user_to_groups() -> None:
    log(f"Adding {TARGET_USER} to the dialout, docker and libvirt groups...")
    for group in ("dialout", "docker", "libvirt"):
        if capture(["getent", "group", group], check=False):
            run(["usermod", "-aG", group, TARGET_USER], sudo=True)


# ---------------------------------------------------------------------------
# TPM 2.0 boot unlock
# ---------------------------------------------------------------------------


def _tpm_device() -> str:
    for node in ("/dev/tpmrm0", "/dev/tpm0"):
        if Path(node).exists():
            return node
    listing = capture(["systemd-cryptenroll", "--tpm2-device=list"], sudo=True, check=False)
    return "auto" if "/dev/tpm" in listing else ""


def _root_luks_device() -> str:
    source = capture(["findmnt", "-n", "-o", "SOURCE", "/"], check=False)
    while source:
        if capture(["lsblk", "-no", "FSTYPE", source], check=False) == "crypto_LUKS":
            return source
        parent = capture(["lsblk", "-no", "PKNAME", source], check=False).splitlines()
        if not parent:
            return ""
        source = f"/dev/{parent[0].strip()}"
    return ""


def _all_luks_devices() -> list[str]:
    devices = []
    for line in capture(["lsblk", "-pnro", "NAME,FSTYPE"], check=False).splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == "crypto_LUKS":
            devices.append(fields[0])
    return devices


def _tpm_enrolled(device: str) -> bool:
    listing = capture(["systemd-cryptenroll", device], sudo=True, check=False)
    return "tpm2" in listing.split()


def _enroll_tpm(device: str, tpm_device: str) -> bool:
    log(f"Enrolling TPM 2.0 (PCR 7) on {device}...")
    warn("You will be prompted for the LUKS passphrase to authorize enrollment.")
    result = run(
        ["systemd-cryptenroll", f"--tpm2-device={tpm_device}", "--tpm2-pcrs=7", device],
        sudo=True,
        check=False,
    )
    if result.returncode != 0:
        warn("TPM 2.0 enrollment failed or was cancelled.")
        return False
    return True


def _prompt_for_luks_device(default: str) -> str:
    if not PROMPT.confirm(
        "Enroll a TPM 2.0 key for automatic LUKS boot unlock? Keep the passphrase as recovery."
    ):
        warn("Skipping TPM enrollment as requested.")
        return ""
    run(["lsblk", "-o", "NAME,FSTYPE,SIZE,TYPE,MOUNTPOINTS"], check=False)
    answer = PROMPT.text(f"Press Enter to use [{default}] or specify partition:", default)
    device = answer.strip() or default
    if device and not device.startswith("/"):
        device = f"/dev/{device}"
    if not Path(device).is_block_device():
        warn(f"{device} is not a block device; skipping TPM enrollment.")
        return ""
    return device


def _rewrite_crypttab(text: str, uuid: str, device: str) -> str | None:
    output = []
    found = False
    for line in text.splitlines():
        fields = line.strip().split()
        if not fields or line.lstrip().startswith("#"):
            output.append(line)
            continue
        if len(fields) < 2 or fields[1] not in (f"UUID={uuid}", f"/dev/disk/by-uuid/{uuid}", device):
            output.append(line)
            continue
        found = True
        keyfile = fields[2] if len(fields) > 2 and fields[2] else "none"
        options = [
            option
            for option in (fields[3].split(",") if len(fields) > 3 else [])
            if option and not option.startswith("tpm2-device=")
        ]
        if "x-initrd.attach" not in options:
            options.append("x-initrd.attach")
        options.append("tpm2-device=auto")
        output.append(f"{fields[0]} {fields[1]} {keyfile} {','.join(options)}")
    return "\n".join(output) + "\n" if found else None


def configure_tpm_boot_unlock(device: str) -> None:
    uuid = capture(["cryptsetup", "luksUUID", device], sudo=True, check=False)
    if not uuid:
        warn(f"Could not read the LUKS UUID for {device}; TPM boot configuration was not changed.")
        return
    crypttab = Path("/etc/crypttab")
    if not crypttab.exists():
        warn("/etc/crypttab was not found; TPM boot configuration was not changed.")
        return
    current = capture(["cat", str(crypttab)], sudo=True)
    updated = _rewrite_crypttab(current, uuid, device)
    if updated is None:
        warn(f"No /etc/crypttab entry matched {device}; TPM boot configuration was not changed.")
        return
    if updated != current:
        write_root_file(crypttab, updated)
        log(f"Configured TPM2 unlocking in /etc/crypttab for {device}.")

    if run(["dracut", "--regenerate-all", "--force"], sudo=True, check=False).returncode:
        warn("Failed to rebuild the Dracut initramfs with TPM2 support.")
        return
    initrd = Path(f"/boot/initrd.img-{platform.release()}")
    listing = capture(["lsinitrd", str(initrd)], sudo=True, check=False)
    if "etc/crypttab" not in listing:
        warn("The initramfs does not contain /etc/crypttab; TPM boot unlocking is not configured.")
        return
    if "libcryptsetup-token-systemd-tpm2.so" not in listing:
        warn("The initramfs does not contain the systemd TPM2 token plugin.")
        return
    log("TPM2 unlocking is configured. The LUKS passphrase remains available for recovery.")


def install_tpm_unlock() -> None:
    log("Checking TPM 2.0 availability and existing enrollment...")
    for module in ("tpm_crb", "tpm_tis"):
        run(["modprobe", module], sudo=True, check=False, quiet=True)

    tpm_device = _tpm_device()
    if not tpm_device:
        warn("No active TPM 2.0 device detected.")
        warn("In a virtual machine, enable the TPM under Settings -> Security.")
        return

    device = _root_luks_device()
    if not device:
        candidates = _all_luks_devices()
        device = candidates[0] if len(candidates) == 1 else ""
    if not device:
        warn("Could not identify the encrypted root partition; skipping TPM enrollment.")
        return

    if _tpm_enrolled(device):
        log(f"A TPM 2.0 key is already enrolled on {device}.")
    else:
        device = _prompt_for_luks_device(device)
        if not device:
            return
        if not _tpm_enrolled(device) and not _enroll_tpm(device, tpm_device):
            return
    configure_tpm_boot_unlock(device)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def print_summary() -> None:
    section("Setup complete")
    warn("Log out and back in (or reboot) for group changes (Docker, dialout, libvirt) to take effect.")


def main() -> int:
    try:
        ensure_normal_user()
        prepare_environment()
    except SetupError as error:
        warn(str(error))
        return 1

    section("Debian headless development machine setup")
    steps = (
        ("package lists", update_and_upgrade),
        ("bootstrap tools", install_bootstrap_tools),
        ("apt repositories", add_repositories),
        ("system packages", install_system_packages),
        ("dracut LUKS/TPM configuration", configure_dracut),
        ("Flatpak apps", install_flatpak_apps),
        ("Google Chrome", install_chrome),
        ("VSCodium", install_vscodium),
        ("Craft apps", install_craft_apps),
        ("Craft app scaling", configure_craft_scaling),
        ("XRDP desktop", configure_xrdp),
        ("fail2ban SSH protection", configure_fail2ban),
        ("GUI toggles", install_gui_toggles),
        ("polkit rule", install_polkit_rule),
        ("Node.js", install_node),
        ("global npm packages", npm_install_globals),
        ("Docker", install_docker),
        ("Google Cloud CLI", install_gcloud),
        ("Cloudflare", install_cloudflare),
        ("shell configuration", configure_shell),
        ("cheat sheet", install_cheatsheet),
        ("user groups", add_user_to_groups),
    )
    optional_steps = (
        ("hardware support packages", install_hardware_support),
        ("Dropbox", install_dropbox),
        ("mitmproxy", install_mitmproxy),
        ("Google Antigravity", install_antigravity),
        ("Astral uv", install_uv),
        ("Tailscale", install_tailscale),
        ("Fresh", install_fresh),
        ("no-more-secrets", install_no_more_secrets),
        ("Kiri", install_kiri),
        ("TPM 2.0", install_tpm_unlock),
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
