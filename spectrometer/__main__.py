"""Command-line entry point; hardware libraries are not required for --help."""

import argparse
import logging


def main():
    parser = argparse.ArgumentParser(description="Spectrometer UI on the SPI LCD and I2C touchscreen")
    parser.add_argument("--windowed", action="store_true", help="Open a desktop preview")
    parser.add_argument("--touch-debug", action="store_true",
                        help="Log raw/mapped touch coordinates and button actions")
    parser.add_argument("--fps", type=float, default=5.0, help="Requested camera fps (default: 5)")
    parser.add_argument("--exposure-us", type=int, help="Manual exposure in microseconds (default: auto)")
    parser.add_argument("--no-camera", action="store_true", help="Run the UI without camera acquisition")
    parser.add_argument("--performance-debug", action="store_true",
                        help="Log camera processing and LCD transfer times")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.touch_debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.performance_debug:
        logging.getLogger("spectrometer.camera").setLevel(logging.DEBUG)
        logging.getLogger("spectrometer.hardware").setLevel(logging.DEBUG)

    from .ui import SpectrometerUI
    from .camera import CameraSettings, CameraStream

    try:
        settings = CameraSettings(frame_rate=args.fps, exposure_us=args.exposure_us)
    except ValueError as exc:
        parser.error(str(exc))

    SpectrometerUI(fullscreen=not args.windowed,
                   camera=None if args.no_camera else CameraStream(settings)).run()


if __name__ == "__main__":
    main()
