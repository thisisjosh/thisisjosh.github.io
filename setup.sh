#!/usr/bin/env bash
#
# =========================================================================
# Usage:
#   wget -qO- https://www.optimumunknown.com/setup.sh | bash
#
# =========================================================================
#
# Debian Headless Dev Setup Script (Complete Toolchain & Non-Interactive Setup)
# Automatically configures XFCE, XRDP, VSCodium, NVM, Docker, Tailscale,
# Cloudflare & GCloud CLIs, Encryption tools, OpenCV, System Monitoring, and Remmina.

set -euo pipefail

# Force non-interactive frontend globally to prevent debconf/ncurses dialogs from locking Hyper-V terminals
export DEBIAN_FRONTEND=noninteractive

# Visual formatting
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[!]${NC} $1"; exit 1; }

# Interactive prompt helpers that work even when script is executed via pipe (e.g. curl ... | bash)
prompt_confirm() {
    local prompt_msg="$1"
    local reply=""
    if [ -t 0 ]; then
        read -p "$prompt_msg " -n 1 -r reply
        echo ""
    elif [ -e /dev/tty ] && [ -r /dev/tty ]; then
        read -p "$prompt_msg " -n 1 -r reply < /dev/tty
        echo ""
    else
        reply="n"
    fi
    [[ "$reply" =~ ^[Yy]$ ]]
}

prompt_input() {
    local prompt_msg="$1"
    local var_name="$2"
    local default_val="${3:-}"
    local user_val=""
    if [ -t 0 ]; then
        read -p "$prompt_msg " user_val
    elif [ -e /dev/tty ] && [ -r /dev/tty ]; then
        read -p "$prompt_msg " user_val < /dev/tty
    else
        user_val="$default_val"
    fi
    printf -v "$var_name" "%s" "${user_val:-$default_val}"
}

# Ensure script is run as normal user (not root directly)
if [ "$(id -u)" -eq 0 ]; then
    err "Please run this script as your normal user, not as root."
fi

TARGET_USER="$USER"
USER_HOME="$HOME"

echo "=========================================================="
echo "  Debian Headless Development Machine Setup Script        "
echo "  (Sudo access configured by Debian installer)           "
echo "=========================================================="
echo ""

# 1. Update and Upgrade System
log "Updating APT package lists and upgrading existing packages..."
sudo DEBIAN_FRONTEND=noninteractive apt update && sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y

# Install essential bootstrap tools for keyrings and downloads
log "Installing essential prerequisites (curl, wget, ca-certificates, gnupg)..."
sudo DEBIAN_FRONTEND=noninteractive apt install -y curl wget ca-certificates gnupg
sudo mkdir -p --mode=0755 /usr/share/keyrings /etc/apt/keyrings

# Add VeraCrypt repository for Debian 13 (matching Debian Trixie)
log "Checking VeraCrypt repository for Debian 13..."
if [ ! -s /usr/share/keyrings/home_unit193_veracrypt.gpg ]; then
    wget -qO- https://download.opensuse.org/repositories/home:unit193:/veracrypt/Debian_13/Release.key | gpg --dearmor | sudo tee /usr/share/keyrings/home_unit193_veracrypt.gpg > /dev/null
fi
if [ ! -f /etc/apt/sources.list.d/home:unit193:veracrypt.list ]; then
    echo 'deb [signed-by=/usr/share/keyrings/home_unit193_veracrypt.gpg] http://download.opensuse.org/repositories/home:/unit193:/veracrypt/Debian_13/ /' | sudo tee /etc/apt/sources.list.d/home:unit193:veracrypt.list > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt update
fi

# 2. Install Core Dependencies, System Utilities, Monitoring, & Encryption
log "Installing core utilities, compilation toolchains, monitoring tools, and security packages..."
# Use Dracut's systemd initramfs so the LUKS2 TPM token is available during boot.
sudo install -d -m 0755 /etc/dracut.conf.d
printf '%s\n' \
    'hostonly="yes"' \
    'hostonly_cmdline="yes"' \
    'install_items+=" /etc/crypttab "' \
    'add_dracutmodules+=" systemd-cryptsetup crypt lvm tpm2-tss "' \
    'omit_dracutmodules+=" console-setup "' \
    | sudo tee /etc/dracut.conf.d/20-luks-tpm.conf > /dev/null
sudo DEBIAN_FRONTEND=noninteractive apt install -y \
    xfce4 \
    xfce4-goodies \
    xorgxrdp \
    xrdp \
    dbus-x11 \
    zram-tools \
    curl \
    wget \
    ca-certificates \
    lsb-release \
    git \
    gh \
    ntfs-3g \
    openssl \
    dracut \
    cryptsetup \
    systemd-cryptsetup \
    build-essential \
    gcc \
    g++ \
    gdb \
    cmake \
    python3 \
    python3-pip \
    python3-venv \
    python3-opencv \
    libopencv-dev \
    jq \
    fzf \
    ripgrep \
    btop \
    tmux \
    bat \
    ncdu \
    ffmpeg \
    vlc \
    handbrake \
    rclone \
    keepassxc \
    encfs \
    veracrypt \
    fscrypt \
    libpam-fscrypt \
    tpm2-tools \
    qdirstat \
    remmina

# Add xrdp user to ssl-cert group to allow access to TLS certs
if getent group ssl-cert >/dev/null 2>&1; then
    sudo usermod -aG ssl-cert xrdp 2>/dev/null || true
fi

# 3. Configure XRDP Session for XFCE (with 2x HiDPI Window Scaling)
log "Configuring XRDP session for XFCE with 2x HiDPI window scaling..."

# Pre-configure XFCE xfconf settings files on disk for 2x scaling
mkdir -p "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml"
if [ ! -f "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml" ]; then
    cat << 'EOF' > "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml"
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xsettings" version="1.0">
  <property name="Gdk" type="empty">
    <property name="WindowScalingFactor" type="int" value="2"/>
  </property>
  <property name="Gtk" type="empty">
    <property name="CursorThemeSize" type="int" value="48"/>
  </property>
</channel>
EOF
else
    if grep -q "WindowScalingFactor" "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml"; then
        sed -i 's/<property name="WindowScalingFactor" type="int" value="[0-9]*"\/>/<property name="WindowScalingFactor" type="int" value="2"\/>/' "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml"
    fi
fi

if [ ! -f "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml" ]; then
    cat << 'EOF' > "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml"
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="theme" type="string" value="Default-xhdpi"/>
  </property>
</channel>
EOF
else
    if grep -q 'name="theme"' "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml"; then
        sed -i 's/<property name="theme" type="string" value="[^"]*"\/>/<property name="theme" type="string" value="Default-xhdpi"\/>/' "${USER_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/xfwm4.xml"
    fi
fi

# Configure .xsession to export HiDPI scaling and enforce xfconf 2x scaling
cat << 'EOF' > "${USER_HOME}/.xsession"
#!/usr/bin/env bash

# HiDPI environment variables for GTK / Qt in RDP sessions
export GDK_SCALE=2
export GDK_DPI_SCALE=0.5
export XCURSOR_SIZE=48

# Background hook to enforce xfconf 2x scaling once xfsettingsd is running
(
    for i in {1..20}; do
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
EOF
chmod +x "${USER_HOME}/.xsession"
chown -R "${TARGET_USER}:${TARGET_USER}" "${USER_HOME}/.config/xfce4" "${USER_HOME}/.xsession" 2>/dev/null || true

# If an active desktop display is present in the current session, apply 2x scaling immediately
if [ -n "${DISPLAY:-}" ] && command -v xfconf-query >/dev/null 2>&1; then
    xfconf-query -c xsettings -p /Gdk/WindowScalingFactor -s 2 --create -t int 2>/dev/null || true
    xfconf-query -c xfwm4 -p /general/theme -s Default-xhdpi --create -t string 2>/dev/null || true
    xfconf-query -c xsettings -p /Gtk/CursorThemeSize -s 48 --create -t int 2>/dev/null || true
fi

if [ -f /etc/xrdp/startwm.sh ]; then
    if ! grep -q "startxfce4" /etc/xrdp/startwm.sh; then
        sudo sed -i 's/test -x \/etc\/X11\/Xsession.*/# test -x \/etc\/X11\/Xsession/' /etc/xrdp/startwm.sh
        sudo sed -i 's/exec \/bin\/sh \/etc\/X11\/Xsession/# exec \/bin\/sh \/etc\/X11\/Xsession/' /etc/xrdp/startwm.sh
        echo "startxfce4" | sudo tee -a /etc/xrdp/startwm.sh > /dev/null
    fi

    # Unset conflicting environment variables in startwm.sh to prevent immediate RDP disconnects
    if ! grep -q "DBUS_SESSION_BUS_ADDRESS" /etc/xrdp/startwm.sh; then
        sudo sed -i '/#!\/bin\/sh/a \
unset DBUS_SESSION_BUS_ADDRESS\
unset XDG_RUNTIME_DIR' /etc/xrdp/startwm.sh
    fi
fi

sudo systemctl enable xrdp --now
sudo systemctl restart xrdp

# 4. Wire Up Manual GUI Toggle Commands ('enable-gui' & 'disable-gui')
log "Setting up 'enable-gui' and 'disable-gui' commands in /usr/local/bin..."
cat << 'EOF' | sudo tee /usr/local/bin/disable-gui > /dev/null
#!/usr/bin/env bash
echo "Disabling local GUI on boot (setting multi-user.target)..."
sudo systemctl set-default multi-user.target
echo "Stopping local display manager..."
sudo systemctl stop display-manager 2>/dev/null || sudo systemctl stop lightdm 2>/dev/null || sudo systemctl stop gdm3 2>/dev/null || sudo systemctl stop sddm 2>/dev/null || true
echo "Local GUI disabled. Default boot target is now multi-user.target (headless)."
EOF
sudo chmod +x /usr/local/bin/disable-gui

cat << 'EOF' | sudo tee /usr/local/bin/enable-gui > /dev/null
#!/usr/bin/env bash
echo "Enabling local GUI on boot (setting graphical.target)..."
sudo systemctl set-default graphical.target
echo "Starting local display manager..."
sudo systemctl start display-manager 2>/dev/null || sudo systemctl start lightdm 2>/dev/null || sudo systemctl start gdm3 2>/dev/null || sudo systemctl start sddm 2>/dev/null || true
echo "Local GUI enabled. Default boot target is now graphical.target."
EOF
sudo chmod +x /usr/local/bin/enable-gui

# 5. Silence Polkit Color Manager Popups
log "Adding Polkit rule to suppress colord prompts over RDP..."
sudo mkdir -p /etc/polkit-1/rules.d/
cat << 'EOF' | sudo tee /etc/polkit-1/rules.d/02-allow-colord.rules > /dev/null
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.color-manager.create-device" ||
         action.id == "org.freedesktop.color-manager.create-profile" ||
         action.id == "org.freedesktop.packagekit.system-sources-refresh") &&
        subject.isInGroup("sudo")) {
        return polkit.Result.YES;
    }
});
EOF

# 6. Install VSCodium (Open Source VS Code Alternative)
log "Installing VSCodium..."
if [ ! -s /usr/share/keyrings/vscodium-archive-keyring.gpg ]; then
    wget -qO - https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/raw/master/pub.gpg | gpg --dearmor | sudo tee /usr/share/keyrings/vscodium-archive-keyring.gpg > /dev/null
fi
if [ ! -f /etc/apt/sources.list.d/vscodium.list ]; then
    echo 'deb [ signed-by=/usr/share/keyrings/vscodium-archive-keyring.gpg ] https://download.vscodium.com/debs vscodium main' | sudo tee /etc/apt/sources.list.d/vscodium.list > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt update
fi
if ! command -v codium >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt install -y codium
else
    log "VSCodium is already installed."
fi

# 7. Install NVM (Node Version Manager) & Node.js LTS
log "Installing NVM (Node Version Manager) & Node.js LTS..."
export NVM_DIR="${USER_HOME}/.nvm"
if [ ! -d "$NVM_DIR" ] || [ ! -s "$NVM_DIR/nvm.sh" ]; then
    rm -rf "$NVM_DIR"
    (unset NVM_DIR && curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash)
fi

# Ensure .bashrc contains the NVM loading script
if ! grep -q "NVM_DIR" "${USER_HOME}/.bashrc" 2>/dev/null; then
    cat << 'EOF' >> "${USER_HOME}/.bashrc"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
EOF
fi

# Temporarily disable strict flags (-e and -u) while loading and using NVM,
# as nvm.sh evaluates uninitialized variables and returns non-zero during internal checks
set +eu
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Install Node.js LTS if no Node version is installed yet
if ! nvm ls --no-colors 2>/dev/null | grep -qE "v[0-9]+"; then
    log "Downloading and installing Node.js LTS via NVM..."
    nvm install --lts
fi

nvm use --lts >/dev/null 2>&1 || true
nvm alias default 'lts/*' >/dev/null 2>&1 || true

# Export node and npm to PATH for current session and create system fallback symlinks in /usr/local/bin
NODE_EXEC="$(nvm which current 2>/dev/null || nvm which default 2>/dev/null || true)"
if [ -n "$NODE_EXEC" ] && [ -x "$NODE_EXEC" ]; then
    NODE_BIN_DIR="$(dirname "$NODE_EXEC")"
    export PATH="$NODE_BIN_DIR:$PATH"
    log "Node.js $(${NODE_EXEC} -v) and npm successfully configured."
    sudo ln -sf "$NODE_BIN_DIR/node" /usr/local/bin/node 2>/dev/null || true
    sudo ln -sf "$NODE_BIN_DIR/npm" /usr/local/bin/npm 2>/dev/null || true
    sudo ln -sf "$NODE_BIN_DIR/npx" /usr/local/bin/npx 2>/dev/null || true
fi
set -euo pipefail

# 8. Install Python UV (Fast Package Manager)
log "Installing Astral uv (Python package installer)..."
export PATH="${USER_HOME}/.local/bin:${USER_HOME}/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
else
    log "Astral uv is already installed ($(uv --version 2>/dev/null || echo 'installed'))."
fi

# 9. Install Docker & Docker Compose Plugin
log "Installing Docker & Docker Compose..."
if [ ! -s /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor | sudo tee /etc/apt/keyrings/docker.gpg > /dev/null
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
fi
if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt update
fi
if ! command -v docker >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    log "Docker is already installed."
fi
sudo usermod -aG docker "${TARGET_USER}" 2>/dev/null || true

# 10. Install Tailscale VPN
log "Installing Tailscale..."
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
else
    log "Tailscale is already installed ($(tailscale version 2>/dev/null | head -n1 || echo 'installed'))."
fi

# 11. Install Google Cloud CLI
log "Installing Google Cloud CLI..."
if [ ! -s /usr/share/keyrings/cloud.google.gpg ]; then
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor | sudo tee /usr/share/keyrings/cloud.google.gpg > /dev/null
fi
if [ ! -f /etc/apt/sources.list.d/google-cloud-sdk.list ]; then
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt update
fi
if ! command -v gcloud >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt install -y google-cloud-cli
else
    log "Google Cloud CLI is already installed."
fi

# 12. Install Cloudflare CLI ('cf') & Cloudflared
log "Installing Cloudflare CLI ('cf') via npm and Cloudflared..."
if ! command -v node >/dev/null 2>&1; then
    set +eu
    [ -s "${USER_HOME}/.nvm/nvm.sh" ] && \. "${USER_HOME}/.nvm/nvm.sh"
    nvm use --lts >/dev/null 2>&1 || true
    set -euo pipefail
fi
if command -v npm >/dev/null 2>&1; then
    if ! command -v cf >/dev/null 2>&1; then
        npm install -g cf
    else
        log "Cloudflare CLI ('cf') is already installed."
    fi
else
    warn "npm command not found; skipping 'cf' CLI npm install."
fi

if [ ! -s /usr/share/keyrings/cloudflare-main.gpg ]; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | gpg --dearmor | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
fi
if [ ! -f /etc/apt/sources.list.d/cloudflared.list ]; then
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list > /dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt update
fi
if ! command -v cloudflared >/dev/null 2>&1; then
    sudo DEBIAN_FRONTEND=noninteractive apt install -y cloudflared
else
    log "Cloudflared daemon is already installed."
fi

# 13. Serial Port & Container Permissions (dialout & docker groups)
log "Ensuring ${TARGET_USER} is in dialout & docker groups..."
for grp in dialout docker; do
    if getent group "$grp" >/dev/null 2>&1; then
        sudo usermod -aG "$grp" "${TARGET_USER}"
    fi
done

# 14. Configure Terminal Cheat Sheet ('h' command) & Aliases
log "Setting up system cheat sheet shortcut ('h') and aliases..."
if ! grep -q "alias bat='batcat'" "${USER_HOME}/.bashrc" 2>/dev/null; then
    echo "alias bat='batcat'" >> "${USER_HOME}/.bashrc"
fi

# Clean up legacy multiline h() function from .bashrc if present so /usr/local/bin/h takes precedence
if grep -q "h()" "${USER_HOME}/.bashrc" 2>/dev/null; then
    sed -i '/# Terminal Tool Cheat Sheet Shortcut/,/^}/d' "${USER_HOME}/.bashrc" 2>/dev/null || true
fi

cat << 'EOF' | sudo tee /usr/local/bin/h > /dev/null
#!/usr/bin/env bash
cat << 'CHEAT'
================================================================================
                      INSTALLED DEV TOOLS CHEAT SHEET                           
================================================================================
[Development & Editors]
  codium              - Open-source, telemetry-free VS Code editor
  nvm                 - Node Version Manager (switch Node versions dynamically)
  node / npm          - Node.js JavaScript runtime & package manager
  gh                  - GitHub command-line client
  uv                  - Ultra-fast Python package installer & resolver (pip replacement)
  python3 / pip3      - Python 3 interpreter and standard package manager
  gcc / g++ / cmake   - C/C++ compilation, build systems, and GDB debugger
  libopencv-dev       - Open Source Computer Vision library C++ headers & Python module

[Containerization & Cloud]
  docker / compose    - Container management platform and multi-container orchestrator
  tailscale           - Zero-config mesh VPN based on WireGuard
  gcloud              - Google Cloud Platform command-line interface
  cf                  - Cloudflare API command-line tool
  cloudflared         - Cloudflare Tunnel daemon for exposing local ports securely

[Encryption & Security]
  fscrypt             - Linux kernel ext4 folder encryption tool
  veracrypt           - Cross-platform file/partition encryption container manager
  encfs               - FUSE-based user-space encrypted filesystem
  keepassxc           - Offline encrypted password manager (.kdbx)
  rclone              - Sync & encrypt files to cloud storage providers (S3, Drive, etc.)
  tpm2-tools          - Hardware TPM 2.0 security module configuration utilities

[Terminal Utilities & Productivity]
  curl / wget         - Command-line HTTP and data transfer utilities
  btop                - Modern, interactive terminal resource & process monitor
  tmux                - Terminal multiplexer for persistent sessions
  fzf                 - Command-line interactive fuzzy finder (Ctrl+R / Ctrl+T)
  ripgrep (rg)        - Ultra-fast recursive line-matching search tool
  bat (batcat)        - Cat clone with syntax highlighting and Git integration
  jq                  - Command-line JSON processor and filter

[Filesystems & Disk Usage]
    ntfs-3g             - Read and write NTFS filesystems
    qdirstat            - Graphical disk usage visualizer
    ncdu                - Interactive terminal disk usage analyzer

[Media & Video Tools]
  ffmpeg              - Command-line audio/video converter and video processor
  vlc                 - Cross-platform media player
  handbrake           - Multi-threaded GUI video converter and compressor

[Remote Access & System]
  remmina             - Feature-rich remote desktop client (RDP, VNC, SSH, SFTP)
  enable-gui          - Switch to graphical.target & start local display manager
  disable-gui         - Switch to multi-user.target (headless) & stop display manager
================================================================================
CHEAT
EOF
sudo chmod +x /usr/local/bin/h

echo ""
echo "=========================================================="
echo "  Automated Post-Install Steps (TPM)                       "
echo "=========================================================="
echo ""

# --- TPM 2.0 Verification & LUKS Enrollment ---
log "Checking TPM 2.0 availability and existing enrollment..."

configure_tpm2_boot_unlock() {
    local luks_device="$1"
    local luks_uuid crypttab_tmp awk_status crypttab_changed initrd_path

    if ! luks_uuid=$(sudo cryptsetup luksUUID "$luks_device" 2>/dev/null); then
        warn "Could not read the LUKS UUID for ${luks_device}; TPM boot configuration was not changed."
        return 1
    fi

    if [ ! -f /etc/crypttab ]; then
        warn "/etc/crypttab was not found; TPM boot configuration was not changed."
        return 1
    fi

    if ! crypttab_tmp=$(mktemp); then
        warn "Could not create a temporary crypttab file; TPM boot configuration was not changed."
        return 1
    fi

    if sudo awk -v uuid="$luks_uuid" -v device="$luks_device" '
        BEGIN { found = 0; changed = 0 }
        /^[[:space:]]*#/ || NF == 0 { print; next }
        $2 == "UUID=" uuid || $2 == "/dev/disk/by-uuid/" uuid || $2 == device {
            found = 1
            old_options = NF >= 4 ? $4 : ""
            option_count = split(old_options, options, ",")
            new_options = ""
            has_initrd_attach = 0
            for (i = 1; i <= option_count; i++) {
                if (options[i] ~ /^tpm2-device=/) {
                    continue
                }
                if (options[i] == "x-initrd.attach") {
                    has_initrd_attach = 1
                }
                if (options[i] != "") {
                    new_options = new_options (new_options == "" ? "" : ",") options[i]
                }
            }
            if (!has_initrd_attach) {
                new_options = new_options (new_options == "" ? "" : ",") "x-initrd.attach"
            }
            new_options = new_options (new_options == "" ? "" : ",") "tpm2-device=auto"
            if (NF < 3 || $3 == "") $3 = "none"
            $4 = new_options
            if (new_options != old_options) changed = 1
            print
            next
        }
        { print }
        END { if (!found) exit 3 }
    ' /etc/crypttab > "$crypttab_tmp"; then
        crypttab_changed=false
    else
        awk_status=$?
        if [ "$awk_status" -eq 3 ]; then
            rm -f "$crypttab_tmp"
            warn "No /etc/crypttab entry matched ${luks_device} (UUID=${luks_uuid}); TPM boot configuration was not changed."
            return 1
        elif [ "$awk_status" -eq 4 ]; then
            crypttab_changed=false
        else
            rm -f "$crypttab_tmp"
            warn "Could not process /etc/crypttab; TPM boot configuration was not changed."
            return 1
        fi
    fi

    if grep -F "tpm2-device=auto" "$crypttab_tmp" >/dev/null && ! cmp -s "$crypttab_tmp" /etc/crypttab; then
        crypttab_changed=true
        if ! sudo install -o root -g root -m 0644 "$crypttab_tmp" /etc/crypttab; then
            rm -f "$crypttab_tmp"
            warn "Could not configure TPM unlocking in /etc/crypttab."
            return 1
        fi
        log "Configured TPM2 unlocking in /etc/crypttab for ${luks_device}."
    fi
    rm -f "$crypttab_tmp"

    if ! sudo dracut --regenerate-all --force; then
        warn "Failed to rebuild the Dracut initramfs with TPM2 support."
        return 1
    fi

    initrd_path="/boot/initrd.img-$(uname -r)"
    if ! sudo lsinitrd "$initrd_path" | grep -F 'etc/crypttab' >/dev/null; then
        warn "The current Dracut initramfs does not contain /etc/crypttab; TPM boot unlocking is not configured."
        return 1
    fi
    if ! sudo lsinitrd "$initrd_path" | grep -F 'libcryptsetup-token-systemd-tpm2.so' >/dev/null; then
        warn "The current Dracut initramfs does not contain the systemd TPM2 token plugin."
        return 1
    fi

    log "Dracut initramfs contains the TPM2 crypttab entry and token plugin. The LUKS passphrase remains available for recovery."
}

# Ensure TPM kernel modules are loaded if available
sudo modprobe tpm_crb 2>/dev/null || true
sudo modprobe tpm_tis 2>/dev/null || true

# Check for actual TPM character devices or populated list from systemd-cryptenroll
TPM_DEV_NODE=""
if [ -c /dev/tpmrm0 ]; then
    TPM_DEV_NODE="/dev/tpmrm0"
elif [ -c /dev/tpm0 ]; then
    TPM_DEV_NODE="/dev/tpm0"
elif sudo systemd-cryptenroll --tpm2-device=list 2>/dev/null | grep -q "/dev/tpm"; then
    TPM_DEV_NODE="auto"
fi

TPM_ENROLLED_DEV=""
if [ -n "$TPM_DEV_NODE" ]; then
    # Automatically trace the root filesystem mount (/) back to its underlying physical LUKS partition
    LUKS_DEV=""
    DEV_TRACK=$(findmnt -n -o SOURCE / 2>/dev/null || true)
    while [ -n "$DEV_TRACK" ]; do
        FSTYPE_CHECK=$(lsblk -no FSTYPE "$DEV_TRACK" 2>/dev/null || true)
        if [ "$FSTYPE_CHECK" = "crypto_LUKS" ]; then
            LUKS_DEV="$DEV_TRACK"
            break
        fi
        PARENT_NAME=$(lsblk -no PKNAME "$DEV_TRACK" 2>/dev/null | head -n1 || true)
        if [ -n "$PARENT_NAME" ]; then
            DEV_TRACK="/dev/$PARENT_NAME"
        else
            break
        fi
    done

    # Collect all crypto_LUKS candidate partitions across the system
    mapfile -t ALL_LUKS < <(lsblk -pnro NAME,FSTYPE 2>/dev/null | awk '$2 == "crypto_LUKS" {print $1}')
    if [ -z "$LUKS_DEV" ] && [ "${#ALL_LUKS[@]}" -eq 1 ]; then
        LUKS_DEV="${ALL_LUKS[0]}"
    fi

    # Check if detected device already has TPM 2.0 enrolled
    if [ -n "$LUKS_DEV" ] && sudo systemd-cryptenroll "$LUKS_DEV" 2>/dev/null | grep -qw "tpm2"; then
        log "A TPM 2.0 key is already enrolled on ${LUKS_DEV}."
        TPM_ENROLLED_DEV="$LUKS_DEV"
    else
        echo ""
        if prompt_confirm "Enroll a TPM 2.0 key for automatic LUKS boot unlock? Keep the passphrase as recovery. (y/N)"; then
            echo ""
            echo "Candidate encrypted partition(s) found (crypto_LUKS):"
            lsblk -o NAME,FSTYPE,SIZE,TYPE,MOUNTPOINTS | grep -E "NAME|crypto_LUKS" || lsblk -f
            echo ""

            if [ -n "$LUKS_DEV" ]; then
                log "Automatically identified encrypted partition: ${LUKS_DEV}"
                prompt_input "Press Enter to use [${LUKS_DEV}] or specify partition:" TARGET_PART "$LUKS_DEV"
            else
                prompt_input "Enter encrypted partition (e.g., /dev/sda3):" TARGET_PART ""
            fi

            # Normalize partition name if entered without /dev/ prefix (e.g., 'sda3' -> '/dev/sda3')
            if [[ -n "$TARGET_PART" && "$TARGET_PART" != /* ]]; then
                TARGET_PART="/dev/$TARGET_PART"
            fi

            if [ -b "$TARGET_PART" ]; then
                if sudo systemd-cryptenroll "$TARGET_PART" 2>/dev/null | grep -qw "tpm2"; then
                    log "A TPM 2.0 key is already enrolled on ${TARGET_PART}."
                    TPM_ENROLLED_DEV="$TARGET_PART"
                else
                    log "Enrolling TPM 2.0 (PCRs 7) to ${TARGET_PART}..."
                    echo "Note: You will be prompted for your LUKS recovery passphrase to authorize this enrollment."
                    if [ -e /dev/tty ] && [ -r /dev/tty ]; then
                        if sudo systemd-cryptenroll --tpm2-device="$TPM_DEV_NODE" --tpm2-pcrs=7 "$TARGET_PART" < /dev/tty; then
                            log "TPM 2.0 key successfully enrolled on ${TARGET_PART}."
                            TPM_ENROLLED_DEV="$TARGET_PART"
                        else
                            warn "TPM 2.0 enrollment failed or was cancelled. Skipping TPM enrollment."
                        fi
                    else
                        if sudo systemd-cryptenroll --tpm2-device="$TPM_DEV_NODE" --tpm2-pcrs=7 "$TARGET_PART"; then
                            log "TPM 2.0 key successfully enrolled on ${TARGET_PART}."
                            TPM_ENROLLED_DEV="$TARGET_PART"
                        else
                            warn "TPM 2.0 enrollment failed. Skipping TPM enrollment."
                        fi
                    fi
                fi
            else
                warn "Invalid partition specified (${TARGET_PART}). Skipping TPM enrollment."
            fi
        else
            warn "Skipping TPM enrollment as requested."
        fi
    fi
else
    warn "No active TPM 2.0 device (/dev/tpmrm0 or /dev/tpm0) detected."
    warn "If running in a virtual machine (e.g. Hyper-V):"
    warn "  Enable TPM in VM Settings -> Security -> 'Enable Trusted Platform Module'."
fi

if [ -n "$TPM_ENROLLED_DEV" ]; then
    configure_tpm2_boot_unlock "$TPM_ENROLLED_DEV" || true
fi

echo ""

echo ""
echo "=========================================================="
echo "  Setup Complete Successfully!                            "
echo "=========================================================="
warn "Please log out and back in (or reboot) for user group permissions (Docker, dialout) to take effect."
echo "=========================================================="
