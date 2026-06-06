#!/usr/bin/env bash
set -e

INSTALL_DIR="$HOME/ax206-usb-display"
REPO_URL="https://github.com/ffrafat/ax206-display-linux.git"

echo "=== Installing ax206-smartcool-sysmon-linux ==="

# 1. Install git if missing
if ! command -v git &> /dev/null; then
    echo "Installing Git..."
    sudo apt update && sudo apt install -y git
fi

# 2. Clone repository if not already in target directory
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Cloning repository to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# 3. Install system dependencies
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y python3-venv python3-dev libusb-1.0-0-dev build-essential fonts-liberation

# 4. Setup python virtual environment
echo "Setting up Python virtual environment..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install pyusb pillow numpy psutil

# 5. Configure USB udev rules
echo "Configuring udev permissions..."
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1908", ATTR{idProduct}=="0102", MODE="0666"' | sudo tee /etc/udev/rules.d/99-ax206.rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# 6. Configure systemd daemon service
echo "Setting up systemd service..."
cat << EOF | sudo tee /etc/systemd/system/ax206.service
[Unit]
Description=AX206 USB Display System Monitor
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python sysdash.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ax206.service
sudo systemctl restart ax206.service

echo "=== Installation Complete! ==="
echo "1. Please UNPLUG and REPLUG the display's USB cable to apply permissions."
echo "2. Check status:  sudo systemctl status ax206"
echo "3. Read live logs: sudo journalctl -u ax206 -f"
