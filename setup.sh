#!/bin/bash
set -e
sudo apt-get update
sudo apt-get install -y libsdl2-dev libffi-dev libssl-dev build-essential
sudo apt-get install -y python3-pip python3-smbus python3-spidev python3-gpiozero python3-rpi.gpio
sudo apt-get install -y python3-pil python3-pygame python3-numpy python3-opencv
sudo cp spectrometer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable spectrometer.service
sudo systemctl start spectrometer.service
