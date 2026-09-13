# Raspberry pi configuration

First, disable the activity light to eliminate a source of stray light.

```vi /boot/firmware/config.txt
dtparam=act_led_trigger=none
```

Enable SPI and I2C in `sudo raspi-config` and reboot if prompted. The runtime user
needs access to the `spi`, `i2c`, and `gpio` groups. 

Finally, hook up the waveshare device according to instructions at 

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


Rendering uses Pygame software surfaces and Pillow to pass RGB images to the
LCD driver's RGB565 conversion. Camera acquisition starts by default. Use Raspberry
Pi OS Bookworm or later and install the updated dependencies with `setup.sh`.
Stop any separate camera server before starting this app; only one process can
own the camera.

```sh
python3 -m spectrometer --fps 30 --exposure-us 20000
```

Omit `--exposure-us` for automatic exposure. Full-resolution IMX477 capture is
limited to about 10 fps; the requested rate is capped to the sensor mode's limit.
Longer exposures can reduce it further. Startup logs report the configured frame
period and first-frame exposure. Use `--no-camera` for touch-only diagnostics or
`--windowed --no-camera` for a desktop preview without Pi hardware.


## Touch diagnostics
```sh
python3 -m spectrometer --touch-debug
```

Touch and release each button. Logs show raw controller coordinates, mapped UI coordinates, releases, and placeholder button actions. Button centers are approximately `(82, 288)`, `(240, 288)`, and `(397, 288)`. If there are no touch logs, the controller is not reporting contacts; if mapped coordinates miss these locations, the panel needs a different coordinate mapping. Exit with Ctrl+C, then run `sudo systemctl start spectrometer` to restore the service.
