#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/ax206-usb-display"

if [ ! -d "$INSTALL_DIR" ]; then
    echo "Error: Installation directory $INSTALL_DIR not found."
    exit 1
fi

cd "$INSTALL_DIR"
echo "=== Updating ax206-smartcool-sysmon-linux ==="

# Stop background service so USB is released
echo "Stopping AX206 background service..."
sudo systemctl stop ax206 || true

# Pull updates
echo "Pulling latest commits from git..."
git pull

# Re-run virtual environment pip to catch library additions
echo "Updating Python virtualenv packages..."
.venv/bin/pip install --upgrade pyusb pillow numpy psutil

# Restart service
echo "Restarting AX206 background service..."
sudo systemctl daemon-reload
sudo systemctl start ax206

echo "=== Update Complete! ==="
