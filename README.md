# Raspberry pi configuration

First, we need to disable the activity light to eliminate a source of stray light and add an overlay to use the external LED as a power indicator.

Edit `/boot/firmware/config.txt`, find the `[all]` section heading and add:

```ini
[all]
dtparam=act_led_trigger=none
dtoverlay=gpio-led,gpio=26,label=power,trigger=default-on
```

Reboot with `sudo reboot` to apply.

Next, enable SPI and I2C in `sudo raspi-config` and reboot if prompted. The runtime user
needs access to the `spi`, `i2c`, and `gpio` groups.

Finally, hook up the waveshare device according to instructions [here](https://www.waveshare.com/wiki/3.5inch_Capacitive_Touch_LCD).

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


Rendering passes Pygame RGB buffers directly to the LCD driver's RGB565
conversion. Camera acquisition starts by default. Use Raspberry
Pi OS Bookworm or later and install the updated dependencies with `setup.sh`.
Stop any separate camera server before starting this app; only one process can
own the camera.

```sh
python3 -m spectrometer --fps 5 --frame-averaging 3 --exposure-us 20000
```

Settings are loaded from `~/.spectrometer_config` on startup; the file is created
with defaults (5 fps, three-frame averaging, 4056×3040, automatic exposure) when absent. Camera settings,
calibration labels, and the sensor bounding box persist between runs. `--fps` and
`--frame-averaging`, and `--exposure-us` save new values; use `--auto-exposure` to restore automatic exposure.

Full-resolution IMX477 capture is
limited to about 10 fps; the requested rate is capped to the sensor mode's limit.
Longer exposures can reduce it further. Startup logs report the configured frame
period and first-frame exposure. Use `--no-camera` for touch-only diagnostics or
`--windowed --no-camera` for a desktop preview without Pi hardware.


## REST API

The application serves the API at `http://<pi-address>:8000`.
`python3 -m spectrometer.server` runs only the API, without a camera.
Spectrum IDs are integer timestamps in milliseconds since the Unix epoch.

### GET /capture?name=<name>

Saves a spectrum locally and returns its ID as a JSON integer after saving.
Provide one URL-encoded name of 1–64 characters, for example
`/capture?name=My%20spectrum`.

Returns 400 for an invalid name, 409 when the camera is paused or busy, 503
when no camera is attached, or 500 on capture failure. A 504 means the request
timed out; the capture may still finish saving.

### GET /list

Returns a JSON array of saved spectra, newest first. Each entry contains `id`,
`name`, and `timestamp` formatted as `YYYY-MM-dd HH:mm:ss`. Returns `[]` when
there are no captures. Older unnamed captures use `Unnamed spectrum`;
unreadable files are skipped.
Metadata is kept in a rebuildable `.spectrometer_index.sqlite3` catalog beside
the captures. Existing captures are read once when first indexed; later lists do
not deserialize their raw camera images.

### GET /spectrum/<id>

Returns the full saved spectrum as JSON, including `filename`, name, timestamp,
instrument settings, saved calibration, intensities, sensor area, and image data.
Timestamps use ISO 8601; intensities are arrays; calibration pixel keys are
strings. Binary image data uses base64, with `dtype` and `shape` also included
for `raw_camera_output` in legacy captures or `averaged_camera_output` in new captures. Returns 404 for an unknown ID or 500 if the matching
spectrum data cannot be returned.
Only one full-spectrum transfer is processed at a time; another concurrent
transfer returns 503 to protect memory on the Pi Zero.

### DELETE /spectrum/<id>

Deletes the corresponding saved file. Returns 204 with an empty body on success,
404 for an unknown ID, or 500 if deletion fails.

### GET /settings

Returns the current saved configuration as a JSON object with two sections:

- `camera`: `frame_rate`, `frame_averaging`, `resolution`, and `exposure_us` (`null` means automatic).
- `calibration`: `scale` pixel/value pairs and the `sensor_area` bounding box.

## Touch diagnostics

```sh
python3 -m spectrometer --touch-debug
```

Touch and release each button. Logs show raw controller coordinates, mapped UI coordinates, releases, and placeholder button actions. Button centers are approximately `(82, 288)`, `(240, 288)`, and `(397, 288)`. If there are no touch logs, the controller is not reporting contacts; if mapped coordinates miss these locations, the panel needs a different coordinate mapping. Exit with Ctrl+C, then run `sudo systemctl start spectrometer` to restore the service.

Use `python3 -m spectrometer --performance-debug` to report rolling camera,
rendering, LCD, and metadata timings every five seconds without per-frame logs.

## Power button

Connect the momentary switch between GPIO 21 (physical pin 40) and GND. The app
enables the internal pull-up and debounces the switch. A short press toggles the
backlight and camera/UI activity on release. Hold for three seconds to open a
shutdown confirmation; Yes shuts down the Pi, and No returns to the application.
Run the updated `setup.sh` once to install the shutdown permission. The Pi remains
running. Pending captures are cancelled on pause; files already being written
finish saving. Press again to resume. This applies to the hardware UI.
