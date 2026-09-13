#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y libsdl2-dev libffi-dev libssl-dev build-essential
sudo apt-get install -y python3-pip python3-smbus python3-spidev python3-gpiozero python3-rpi.gpio
sudo apt-get install -y python3-pil python3-pygame python3-numpy python3-opencv python3-picamera2
sudo cp spectrometer.service /etc/systemd/system/
shutdown_rule=$(mktemp)
trap 'rm -f "$shutdown_rule"' EXIT
printf '%s\n' 'spectrometer ALL=(root) NOPASSWD: /sbin/shutdown now' > "$shutdown_rule"
sudo visudo -cf "$shutdown_rule"
sudo install -o root -g root -m 0440 "$shutdown_rule" /etc/sudoers.d/spectrometer-shutdown
rm "$shutdown_rule"
sudo systemctl daemon-reload
sudo systemctl enable spectrometer.service
sudo systemctl start spectrometer.service
