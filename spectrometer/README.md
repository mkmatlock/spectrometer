# Spectrometer UI outline

A lightweight Pygame 2 shell for the Raspberry Pi Zero W. The layout assumes
landscape **480 × 320** on the [Waveshare 3.5-inch capacitive LCD](https://www.waveshare.com/wiki/3.5inch_Capacitive_Touch_LCD)
(native resolution 320 × 480):

- A large spectrum placeholder (464 × 200).
- A narrow raw camera slice placeholder (464 × 40).
- Capture, Calibrate, and Settings buttons, each 48 pixels tall.

No camera access, spectrum plotting, calibration, or settings screens are
implemented yet. Buttons show press feedback. `SpectrometerUI` accepts optional
`on_capture`, `on_calibrate`, and `on_settings` callbacks for later integration.
The IMX477 is not opened, so this outline can also run without the camera.

## Run

Install the distribution package on the Pi:

```sh
sudo apt install python3-pygame
```

From the repository root, using the system Python:

```sh
python3 -m spectrometer
```

For a desktop preview:

```sh
python3 -m spectrometer --windowed
```

Press Escape or close the preview window to exit. Mouse clicks and single-finger
touches activate buttons on release; releasing outside cancels the action.
The event loop sleeps while idle and redraws only when needed.

## Raspberry Pi OS Lite display setup

OS Lite does not include a desktop session. Before launching, configure the
Waveshare display driver and touch input using the linked vendor instructions,
rotate both display and touch coordinates to landscape, and provide an SDL2
compatible display/input backend (for example, a minimal X server configured
for the LCD). Run the application within that display session. Installing
Pygame alone does not configure this SPI display or its I2C touch controller;
the application does not drive either controller directly. Hardware setup and
touch alignment must be verified on the Pi.

Rendering uses Pygame's software surfaces, without a browser, plotting library,
or continuous refresh loop. See the [Pygame display documentation](https://www.pygame.org/docs/ref/display.html)
for backend configuration. Desktop development requires Pygame 2 as well.
