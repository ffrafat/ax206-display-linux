#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/ax206-usb-display"

echo "=== Uninstalling ax206-smartcool-sysmon-linux ==="

# 1. Stop and disable systemd daemon
echo "Stopping and disabling systemd service..."
sudo systemctl stop ax206 || true
sudo systemctl disable ax206 || true

# 2. Clean system configurations
echo "Removing service file and udev rules..."
sudo rm -f /etc/systemd/system/ax206.service
sudo rm -f /etc/udev/rules.d/99-ax206.rules

# 3. Reload system settings
sudo systemctl daemon-reload
sudo systemctl reset-failed
sudo udevadm control --reload-rules

# 4. Remove installation folder (interactive)
read -p "Do you want to delete the installation directory ($INSTALL_DIR)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Deleting $INSTALL_DIR..."
    rm -rf "$INSTALL_DIR"
fi

echo "=== Uninstallation Complete! ==="
