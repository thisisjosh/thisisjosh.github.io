#!/usr/bin/env python3
"""Provision a disposable Debian 13 container and verify the whole setup.

The container is privileged so systemd, XRDP and fail2ban behave as they do on
a real machine. This checkout is mounted read-only at /repo, so setup.sh
runs its local setup.py exactly like a clone would.

After setup finishes twice (the second run checks idempotency) every component
is verified from inside the container. Results are printed, then the container
is force-stopped and deleted along with the temporary image.
"""

from __future__ import annotations

import importlib.util
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SETUP_DIR = Path(__file__).resolve().parent
REPO_ROOT = SETUP_DIR.parent.parent
TEST_USER = "jdf"
HOME_DIR = f"/home/{TEST_USER}"
RUN_TOKEN = f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
IMAGE = f"setup-verify:{RUN_TOKEN}"
CONTAINER = f"setup-verify-{RUN_TOKEN}"
BUILD_TIMEOUT = 900
SYSTEMD_TIMEOUT = 120
PROVISION_TIMEOUT = 3600

DOCKERFILE = """\
FROM debian:13
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \\
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus sudo \\
      curl ca-certificates gnupg procps iproute2 less nano \\
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash {user} \\
 && printf '{user} ALL=(ALL) NOPASSWD:ALL\\n' > /etc/sudoers.d/{user} \\
 && chmod 0440 /etc/sudoers.d/{user}
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd", "--system"]
"""

USER_ENV = f"""export HOME={HOME_DIR}
export PATH="{HOME_DIR}/.local/bin:{HOME_DIR}/.cargo/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
"""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


def load_provisioner():
    """Import setup.py for its package/app lists without running main()."""
    spec = importlib.util.spec_from_file_location("setup_under_test", SETUP_DIR / "setup.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def docker(*arguments: str, capture: bool = False, check: bool = True, timeout: float | None = None):
    command = ["docker", *arguments]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stdout or ''}")
    return result


def exec_script(script: str, *, user: str = TEST_USER, timeout: float = 180) -> subprocess.CompletedProcess:
    return docker(
        "exec", "-u", user, CONTAINER, "bash", "-c", script,
        capture=True, check=False, timeout=timeout,
    )


def check(
    label: str,
    script: str,
    *,
    user: str = TEST_USER,
    expect: str | None = None,
    timeout: float = 180,
) -> Check:
    full_script = USER_ENV + script if user == TEST_USER else script
    try:
        result = exec_script(full_script, user=user, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Check(label, False, "timed out")
    except Exception as error:  # docker exec failures should not abort the report
        return Check(label, False, str(error))
    output = (result.stdout or "").strip()
    ok = result.returncode == 0 and (expect is None or expect in output)
    if ok:
        return Check(label, True)
    lines = [line for line in output.splitlines() if line.strip()]
    detail = lines[-1] if lines else f"exit code {result.returncode}"
    return Check(label, False, detail[:200])


def installed_script(packages: Sequence[str]) -> str:
    names = " ".join(shlex.quote(package) for package in packages)
    return f"""missing=""
for package in {names}; do
    dpkg-query -W -f='${{Status}}' "$package" 2>/dev/null | grep -q 'install ok installed' \\
        || missing="$missing $package"
done
if [ -n "$missing" ]; then
    echo "missing:$missing"
    exit 1
fi
"""


def build_image() -> None:
    print(f"Building {IMAGE} from debian:13...", flush=True)
    with tempfile.TemporaryDirectory(prefix="setup-verify-build-") as directory:
        Path(directory, "Dockerfile").write_text(DOCKERFILE.format(user=TEST_USER))
        docker("build", "-q", "-t", IMAGE, directory, capture=True, timeout=BUILD_TIMEOUT)


def start_container() -> None:
    print(f"Starting {CONTAINER}...", flush=True)
    docker(
        "run", "-d", "--name", CONTAINER,
        "--privileged", "--cgroupns=host",
        "--tmpfs", "/run", "--tmpfs", "/run/lock",
        "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        "-v", f"{REPO_ROOT}:/repo:ro",
        IMAGE,
        capture=True,
    )


def wait_for_systemd() -> None:
    deadline = time.monotonic() + SYSTEMD_TIMEOUT
    state = ""
    while time.monotonic() < deadline:
        state = (exec_script("systemctl is-system-running || true", user="root", timeout=30).stdout or "").strip()
        if state in {"running", "degraded"}:
            return
        time.sleep(2)
    raise RuntimeError(f"systemd did not start (last state: {state or 'unknown'})")


def provision(log_path: Path, timeout: float) -> tuple[int, float]:
    argv = ["docker", "exec", CONTAINER, "su", "-", TEST_USER, "-c", "cd /repo/deb/dev && bash ./setup.sh"]
    pipeline = "set -o pipefail; " + " ".join(shlex.quote(part) for part in argv)
    pipeline += " 2>&1 | tee " + shlex.quote(str(log_path))
    started = time.monotonic()
    result = subprocess.run(["bash", "-c", pipeline], timeout=timeout)
    return result.returncode, time.monotonic() - started


def tail(path: Path, lines: int = 6) -> str:
    try:
        content = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def verify(provisioner) -> list[Check]:
    checks: list[Check] = []

    def add(label: str, script: str, **kwargs) -> None:
        checks.append(check(label, script, **kwargs))

    # Base system and remote access
    add("systemd is running", "case \"$(systemctl is-system-running || true)\" in running|degraded) exit 0;; *) exit 1;; esac", user="root")
    add("SSH server is active", "systemctl is-active --quiet ssh", user="root")
    add("SSH server starts on boot", "systemctl is-enabled --quiet ssh", user="root")
    add("XRDP service is active", "systemctl is-active --quiet xrdp", user="root")
    add("XRDP listens on port 3389", "ss -ltn 'sport = :3389' | grep -q LISTEN", user="root")
    add("XRDP launches XFCE", "grep -q startxfce4 /etc/xrdp/startwm.sh", user="root")
    add("XRDP session clears stale D-Bus state", "grep -q 'unset DBUS_SESSION_BUS_ADDRESS' /etc/xrdp/startwm.sh", user="root")

    # fail2ban
    add("fail2ban service is active", "systemctl is-active --quiet fail2ban", user="root")
    add("fail2ban starts on boot", "systemctl is-enabled --quiet fail2ban", user="root")
    add(
        "fail2ban sshd jail is running",
        "fail2ban-client status sshd",
        user="root",
        expect="Status for the jail: sshd",
    )
    add("fail2ban jails failed logins after 5 tries", "grep -Eq '^maxretry = 5$' /etc/fail2ban/jail.local", user="root")
    add("fail2ban bans for one hour", "grep -Eq '^bantime = 1h$' /etc/fail2ban/jail.local", user="root")
    add(
        "fail2ban jail.local is root-owned",
        "test \"$(stat -c '%U %a' /etc/fail2ban/jail.local)\" = 'root 644'",
        user="root",
    )

    # Desktop scaling
    add("HiDPI X session script installed", "[ -x \"$HOME/.xsession\" ] && grep -q 'GDK_SCALE=2' \"$HOME/.xsession\"")
    xfconf = f"{HOME_DIR}/.config/xfce4/xfconf/xfce-perchannel-xml"
    add("XFCE window scaling is 2x", f"grep -q 'WindowScalingFactor.*value=\"2\"' {xfconf}/xsettings.xml")
    add("XFCE cursor size is 48", f"grep -q 'CursorThemeSize.*value=\"48\"' {xfconf}/xsettings.xml")
    add("XFCE window theme is HiDPI", f"grep -q 'Default-xhdpi' {xfconf}/xfwm4.xml")
    add("Qt apps scale 2x", "grep -q 'QT_SCALE_FACTOR=2' \"$HOME/.profile\"")

    # System configuration
    add("dracut LUKS/TPM config installed", "grep -q 'systemd-cryptsetup' /etc/dracut.conf.d/20-luks-tpm.conf", user="root")
    add(
        "polkit color rule installed",
        "grep -q 'color-manager.create-device' /etc/polkit-1/rules.d/02-allow-colord.rules",
        user="root",
    )
    add("GUI toggle commands installed", "[ -x /usr/local/bin/enable-gui ] && [ -x /usr/local/bin/disable-gui ]", user="root")
    add("cheat sheet lists fail2ban", "/usr/local/bin/h | grep -q fail2ban", user="root")
    add("bash alias and NVM configured", "grep -q \"alias bat='batcat'\" \"$HOME/.bashrc\" && grep -q NVM_DIR \"$HOME/.bashrc\"")
    add(
        "user is in docker, dialout and libvirt",
        "id -nG | grep -qw docker && id -nG | grep -qw dialout && id -nG | grep -qw libvirt",
    )

    # Every package the setup declares
    for label, packages in provisioner.PACKAGE_GROUPS:
        add(f"packages: {label}", installed_script(packages))

    # Third-party applications
    add("Craft apps installed", installed_script([app for app, _ in provisioner.CRAFT_APPS]))
    add("Pinta (Flatpak) installed", "flatpak info --system com.github.PintaProject.Pinta >/dev/null", user="root")
    add("Google Chrome installed", "google-chrome --version | grep -qi 'Google Chrome'")
    add("VSCodium installed", "command -v codium >/dev/null")
    add("Docker CLI and Compose installed", "docker --version >/dev/null && docker compose version >/dev/null")
    add("Google Cloud CLI installed", "command -v gcloud >/dev/null")
    add("Cloudflared installed", "command -v cloudflared >/dev/null")
    add("Node.js and npm installed", "node --version >/dev/null && npm --version >/dev/null")
    add(
        "global npm packages installed",
        '. "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 && command -v cf && command -v freebuff && command -v clixad',
    )
    add("uv installed", "[ -x \"$HOME/.local/bin/uv\" ] && \"$HOME/.local/bin/uv\" --version >/dev/null")
    add("mitmproxy installed", "[ -x \"$HOME/.local/bin/mitmproxy\" ] && \"$HOME/.local/bin/mitmproxy\" --version >/dev/null")
    add("Dropbox daemon and CLI installed", "[ -d \"$HOME/.dropbox-dist\" ] && [ -x \"$HOME/.local/bin/dropbox\" ]")
    add("Dropbox user service defined", "[ -f \"$HOME/.config/systemd/user/dropbox.service\" ]")
    add("Antigravity IDE installed", "[ -x '/opt/Antigravity IDE/antigravity-ide' ] && [ -f /usr/share/applications/antigravity-ide.desktop ]")
    add("Antigravity CLI installed", "command -v agy >/dev/null")
    add("Tailscale installed", "command -v tailscale >/dev/null")
    add("Fresh installed", "command -v fresh >/dev/null")
    add("no-more-secrets installed", "[ -x /usr/local/bin/nms ] && [ -x /usr/local/bin/sneakers ]")

    return checks


def print_report(checks: list[Check], elapsed: float, logs: Path) -> int:
    failures = [item for item in checks if not item.ok]
    print("\n" + "=" * 70)
    print("  SETUP VERIFICATION RESULTS")
    print("=" * 70)
    for item in checks:
        print(f"{'PASS' if item.ok else 'FAIL':<5} {item.label}")
        if not item.ok and item.detail:
            print(f"      {item.detail}")
    print("-" * 70)
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"{len(checks) - len(failures)} passed, {len(failures)} failed in {minutes}m {seconds:02d}s")
    print(f"Logs: {logs}")
    print("=" * 70)
    return 1 if failures else 0


def cleanup() -> None:
    print(f"\nStopping and deleting container {CONTAINER}...", flush=True)
    docker("rm", "-f", CONTAINER, check=False, capture=True)
    print(f"Removing image {IMAGE}...", flush=True)
    docker("rmi", "-f", IMAGE, check=False, capture=True)


def main() -> int:
    if shutil.which("docker") is None:
        print("docker was not found; install Docker to run this test.", file=sys.stderr)
        return 2
    if docker("info", capture=True, check=False).returncode != 0:
        print("Cannot talk to the Docker daemon (is it running and are you in the docker group?).", file=sys.stderr)
        return 2

    provisioner = load_provisioner()
    logs = Path(tempfile.mkdtemp(prefix="setup-verify-logs-"))
    checks: list[Check] = []
    return_code = 0
    started = time.monotonic()

    try:
        build_image()
        start_container()
        wait_for_systemd()

        print("\nRunning setup.sh (first pass)...\n", flush=True)
        code, duration = provision(logs / "run1.log", PROVISION_TIMEOUT)
        if code != 0:
            raise RuntimeError(
                f"setup.sh exited with {code} after {duration:.0f}s\n{tail(logs / 'run1.log')}"
            )
        print(f"\nFirst pass finished in {duration:.0f}s; re-running to check idempotency...\n", flush=True)

        second_code, second_duration = provision(logs / "run2.log", PROVISION_TIMEOUT)
        second_log = (logs / "run2.log").read_text(errors="replace")
        reuses_installed = (
            second_code == 0
            and "All requested packages are already installed." in second_log
            and "photocraft" in second_log
            and "is already installed." in second_log
        )
        checks.append(
            Check(
                "second run succeeds and skips installed work",
                reuses_installed,
                f"exit {second_code} after {second_duration:.0f}s",
            )
        )
        print(f"\nSecond pass finished in {second_duration:.0f}s; verifying the system...\n", flush=True)

        checks.extend(verify(provisioner))
        return_code = 1 if any(not item.ok for item in checks) else 0
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        checks.append(Check("test run completed", False, "interrupted"))
        return_code = 130
    except Exception as error:
        checks.append(Check("test run completed", False, str(error).replace("\n", " ")[:400]))
        return_code = 1
    finally:
        return_code = print_report(checks, time.monotonic() - started, logs) or return_code
        cleanup()

    return return_code


if __name__ == "__main__":
    sys.exit(main())
