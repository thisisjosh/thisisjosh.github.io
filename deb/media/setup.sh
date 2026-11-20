#!/usr/bin/env bash
#
# =========================================================================
# Usage:
#   wget -qO- https://www.optimumunknown.com/deb/media/setup.sh | bash
#
# This is only a bootstrap: it ensures Python 3 is available, then hands the
# rest of the machine setup to setup.py
# (https://www.optimumunknown.com/deb/media/setup.py).
# Set SETUP_PY_URL to run a copy from somewhere else.
# =========================================================================

set -euo pipefail

SETUP_PY_URL="${SETUP_PY_URL:-https://www.optimumunknown.com/deb/media/setup.py}"

if [ "$(id -u)" -eq 0 ]; then
    echo "Please run this script as your normal user, not as root." >&2
    exit 1
fi

# When this script lives in a checkout, run the setup.py sitting next to it.
script_dir=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

ensure_python() {
    command -v python3 >/dev/null 2>&1 && return
    echo "[+] Installing Python 3..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ca-certificates curl
}

ensure_curl() {
    command -v curl >/dev/null 2>&1 && return
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
}

ensure_python

downloaded_script=""
cleanup() {
    if [ -n "$downloaded_script" ]; then
        rm -f "$downloaded_script"
    fi
}
trap cleanup EXIT

if [ -n "$script_dir" ] && [ -f "${script_dir}/setup.py" ]; then
    python_script="${script_dir}/setup.py"
else
    ensure_curl
    downloaded_script="$(mktemp --suffix=.py)"
    python_script="$downloaded_script"
    echo "[+] Downloading setup.py..."
    curl -fsSL "$SETUP_PY_URL" -o "$python_script"
fi

# This bootstrap may itself be arriving on stdin (wget ... | bash), so give
# Python a real terminal for prompts instead of the leftover script stream.
if (exec </dev/tty) 2>/dev/null; then
    python3 "$python_script" "$@" < /dev/tty
else
    python3 "$python_script" "$@" < /dev/null
fi
