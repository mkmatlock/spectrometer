# Raspberry pi configuration

First, disable the activity light to eliminate a source of stray light.

```vi /boot/firmware/config.txt
dtparam=act_led_trigger=none
```

Enable SPI and I2C in `sudo raspi-config` and reboot if prompted. The runtime user
needs access to the `spi`, `i2c`, and `gpio` groups. 


# Set up the hardware



# Install the software

Fill out the dotenv:

```vi .env
HOST="RPI_IP_ADDRESS"
USER="RPI_USERNAME"
PORT="22"
```

# Run the spectrometer software

Install the distribution package on the Pi:

```sh
sudio ./setup.sh
```

This will start a systemd service spectrometer.service that will keep the spectrometer app running in the background at all times. 

## Raspberry Pi OS Lite hardware setup


The supplied drivers use BCM pins 27 (LCD reset), 25 (LCD data/command), 18
(backlight), 4 (touch interrupt), and 17 (touch reset), in addition to the SPI and
I2C bus pins. Wiring must match these assignments. Do not run another display
service or framebuffer driver that owns the same SPI device or GPIO pins.

The LCD driver selects landscape orientation. Touch coordinates are mapped from
its native portrait coordinates to match: `(landscape_x, landscape_y) =
(raw_y, 319 - raw_x)`. The supplied touch driver already performs the X inversion,
so the hardware adapter only swaps its axes. Verify alignment at each button on
the physical panel.

Rendering uses Pygame software surfaces and Pillow to pass RGB images to the
LCD driver's RGB565 conversion. No camera access or acquisition is started.
