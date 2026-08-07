#!/bin/bash
# multiACE Installer for Snapmaker U1
# Usage: Copy multiace/ folder to printer, then run:
#   bash install_multiace.sh

set -e

# Fix Windows line endings in all scripts
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
find "$INSTALL_DIR" -name "*.sh" -exec sed -i 's/\r$//' {} +

HOME_DIR="/home/lava"
EXTRAS_DIR="${HOME_DIR}/klipper/klippy/extras"
KINEMATICS_DIR="${HOME_DIR}/klipper/klippy/kinematics"
CONFIG_DIR="${HOME_DIR}/printer_data/config/extended"
MULTIACE_DIR="${CONFIG_DIR}/multiace"
PRINTER_CFG="${HOME_DIR}/printer_data/config/printer.cfg"
LOGFILE="/tmp/multiace_install.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [multiACE] $1" | tee -a "$LOGFILE"
}

log "=== multiACE Installation ==="
log "Install from: $INSTALL_DIR"
log "Klipper extras: $EXTRAS_DIR"
log "Klipper kinematics: $KINEMATICS_DIR"
log "Config dir: $CONFIG_DIR"

# --- Verify source files exist ---
for f in \
    "klipper/extras/ace.py" \
    "klipper/extras/ace_keepalive.py" \
    "klipper/extras/ace_status.py" \
    "klipper/extras/ace_protocol.py" \
    "klipper/extras/ace_protocol_v1.py" \
    "klipper/extras/ace_protocol_v2.py" \
    "klipper/extras/manual_heads.py" \
    "klipper/extras/filament_feed_ace.py" \
    "klipper/extras/filament_switch_sensor_ace.py" \
    "klipper/kinematics/extruder_ace.py" \
    "config/extended/ace.cfg" \
    "config/extended/multiace/ace_mode_switch.sh" \
    "config/extended/multiace/ace_vars.cfg"
do
    if [ ! -f "$INSTALL_DIR/$f" ]; then
        log "ERROR: Missing file: $f"
        exit 1
    fi
done
log "All source files found"

# --- Verify target directories exist ---
for d in "$EXTRAS_DIR" "$KINEMATICS_DIR" "$CONFIG_DIR"; do
    if [ ! -d "$d" ]; then
        log "ERROR: Target directory not found: $d"
        exit 1
    fi
done
log "Target directories verified"

# --- Backup current files (only if backup doesn't exist yet) ---
log "Backing up current files..."
for f in "filament_feed.py" "filament_switch_sensor.py"; do
    if [ -f "$EXTRAS_DIR/$f" ] && [ ! -f "$EXTRAS_DIR/${f%.py}_pre_multiace.py" ]; then
        cp "$EXTRAS_DIR/$f" "$EXTRAS_DIR/${f%.py}_pre_multiace.py"
        log "  Backed up $f -> ${f%.py}_pre_multiace.py"
    fi
done
if [ -f "$KINEMATICS_DIR/extruder.py" ] && [ ! -f "$KINEMATICS_DIR/extruder_pre_multiace.py" ]; then
    cp "$KINEMATICS_DIR/extruder.py" "$KINEMATICS_DIR/extruder_pre_multiace.py"
    log "  Backed up extruder.py -> extruder_pre_multiace.py"
fi
if [ -f "$CONFIG_DIR/ace.cfg" ] && [ ! -f "$CONFIG_DIR/ace_pre_multiace.cfg" ]; then
    cp "$CONFIG_DIR/ace.cfg" "$CONFIG_DIR/ace_pre_multiace.cfg"
    log "  Backed up ace.cfg -> ace_pre_multiace.cfg"
fi

# --- Copy files ---
log "Installing multiACE files..."

# Klipper extras — copy EVERY ace-side module via glob so this list can never go
# stale again. ace.py imports siblings (ace_keepalive, ace_status, manual_heads);
# a hardcoded subset silently drops them and Klipper fails to boot with
# "No module named 'extras.ace_keepalive'".
for src in "$INSTALL_DIR"/klipper/extras/*.py; do
    base="$(basename "$src")"
    cp "$src" "$EXTRAS_DIR/$base"
    chmod 644 "$EXTRAS_DIR/$base"
done
log "  Klipper extras installed ($(ls -1 "$INSTALL_DIR"/klipper/extras/*.py | wc -l) modules)"

# Klipper kinematics — glob too, for the same reason
for src in "$INSTALL_DIR"/klipper/kinematics/*.py; do
    base="$(basename "$src")"
    cp "$src" "$KINEMATICS_DIR/$base"
    chmod 644 "$KINEMATICS_DIR/$base"
done
log "  Klipper kinematics installed"

# Config
cp "$INSTALL_DIR/config/extended/ace.cfg" "$CONFIG_DIR/ace.cfg"
chmod 644 "$CONFIG_DIR/ace.cfg"
log "  ace.cfg installed"

# multiace directory
mkdir -p "$MULTIACE_DIR"
cp "$INSTALL_DIR/config/extended/multiace/ace_mode_switch.sh" "$MULTIACE_DIR/ace_mode_switch.sh"
chmod +x "$MULTIACE_DIR/ace_mode_switch.sh"
# Only copy ace_vars.cfg if it doesn't exist (preserve settings)
if [ ! -f "$MULTIACE_DIR/ace_vars.cfg" ]; then
    cp "$INSTALL_DIR/config/extended/multiace/ace_vars.cfg" "$MULTIACE_DIR/ace_vars.cfg"
    log "  ace_vars.cfg created (fresh)"
else
    log "  ace_vars.cfg exists, keeping current settings"
fi
log "  multiace config installed"

# Uninstall script
if [ -f "$INSTALL_DIR/uninstall_multiace.sh" ]; then
    cp "$INSTALL_DIR/uninstall_multiace.sh" "$MULTIACE_DIR/uninstall_multiace.sh"
    chmod +x "$MULTIACE_DIR/uninstall_multiace.sh"
    log "  Uninstall script installed"
fi

# Tools (optional)
if [ -d "$INSTALL_DIR/tools" ]; then
    mkdir -p "${HOME_DIR}/printer_data/config/tools"
    cp "$INSTALL_DIR/tools/"*.py "${HOME_DIR}/printer_data/config/tools/" 2>/dev/null || true
    log "  Tools installed"
fi

# --- Clear Python cache ---
find "$EXTRAS_DIR/__pycache__" -name "ace*" -delete 2>/dev/null || true
find "$EXTRAS_DIR/__pycache__" -name "ace_protocol*" -delete 2>/dev/null || true
find "$EXTRAS_DIR/__pycache__" -name "filament_feed*" -delete 2>/dev/null || true
find "$EXTRAS_DIR/__pycache__" -name "filament_switch_sensor*" -delete 2>/dev/null || true
find "$KINEMATICS_DIR/__pycache__" -name "extruder*" -delete 2>/dev/null || true
log "Python cache cleared"

# --- Add include to printer.cfg if not present ---
if [ -f "$PRINTER_CFG" ]; then
    if ! grep -q "extended/ace.cfg" "$PRINTER_CFG"; then
        # Try inserting before first [section], fallback to top of file
        if grep -q '^\[' "$PRINTER_CFG"; then
            sed -i '0,/^\[/{s/^\[/[include extended\/ace.cfg]\n\n[/}' "$PRINTER_CFG"
        else
            sed -i '1i [include extended/ace.cfg]\n' "$PRINTER_CFG"
        fi
        # Verify it was added
        if grep -q "extended/ace.cfg" "$PRINTER_CFG"; then
            log "Added [include extended/ace.cfg] to printer.cfg"
        else
            # Last resort: append to end
            echo -e '\n[include extended/ace.cfg]' >> "$PRINTER_CFG"
            log "Added [include extended/ace.cfg] to end of printer.cfg"
        fi
    else
        log "printer.cfg already includes ace.cfg"
    fi
else
    log "WARNING: printer.cfg not found at $PRINTER_CFG"
fi

# --- Fix line endings on mode switch script ---
sed -i 's/\r$//' "$MULTIACE_DIR/ace_mode_switch.sh"
chmod +x "$MULTIACE_DIR/ace_mode_switch.sh"
log "Mode switch script prepared"

# --- Activate ACE mode (swap files) ---
log "Activating ACE file swap..."
bash "$MULTIACE_DIR/ace_mode_switch.sh" ace
log "ACE files activated"

# --- Delete Python cache completely ---
rm -rf "$EXTRAS_DIR/__pycache__"
rm -rf "$KINEMATICS_DIR/__pycache__"
log "Python cache deleted"

# --- Install boot page-cache prewarm hook (S59, before S60klipper) ---
# Ported from decay71 0.99.6.2b: reads the klipper tree into page cache at boot
# so cold page faults don't trip the multi-MCU homing window ("Timer too close"
# / 0003). Opt-out: touch "$MULTIACE_DIR/prewarm.disabled". Needs root for
# /etc/init.d; a non-root (web-context) install skips it with a note.
if [ "$(id -u)" = "0" ]; then
    if [ -f "$INSTALL_DIR/deploy/S59multiace-prewarm" ]; then
        cp "$INSTALL_DIR/deploy/S59multiace-prewarm" /etc/init.d/S59multiace-prewarm
        sed -i 's/\r$//' /etc/init.d/S59multiace-prewarm
        chmod 755 /etc/init.d/S59multiace-prewarm
        log "  Installed boot prewarm hook: /etc/init.d/S59multiace-prewarm"
    else
        log "  Prewarm hook source not found (deploy/S59multiace-prewarm); skipping"
    fi
else
    log "  Skipped prewarm hook (need root for /etc/init.d); rerun as root to enable"
fi

# --- Optional: Install web console ---
WEB_INSTALL_DIR="${INSTALL_DIR%/multiace}/multiace_web"
if [ -d "$WEB_INSTALL_DIR/install" ] && [ -f "$WEB_INSTALL_DIR/install/install_web.sh" ]; then
    log "Installing multiACE Web Console..."
    if bash "$WEB_INSTALL_DIR/install/install_web.sh"; then
        log "  Web console installed (http://<printer-ip>/multiace/)"
    else
        log "  WARNING: Web console install failed; continuing without it"
    fi
else
    log "Web console source not found, skipping"
fi

log ""
log "=== Installation complete ==="
log "Please reboot the printer to activate multiACE."
log ""
