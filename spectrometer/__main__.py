"""Command-line entry point; hardware libraries are not required for --help."""

import argparse
import logging
from copy import deepcopy


def main():
    parser = argparse.ArgumentParser(description="Spectrometer UI on the SPI LCD and I2C touchscreen")
    parser.add_argument("--windowed", action="store_true", help="Open a desktop preview")
    parser.add_argument("--touch-debug", action="store_true",
                        help="Log raw/mapped touch coordinates and button actions")
    parser.add_argument("--fps", type=float, help="Save requested camera fps (default: stored value, initially 5)")
    parser.add_argument("--frame-averaging", type=int,
                        help="Save rolling frame count (default: stored value, initially 3)")
    exposure = parser.add_mutually_exclusive_group()
    exposure.add_argument("--exposure-us", type=int, help="Save manual exposure in microseconds")
    exposure.add_argument("--auto-exposure", action="store_true", help="Save automatic exposure mode")
    parser.add_argument("--no-camera", action="store_true", help="Run the UI without camera acquisition")
    parser.add_argument("--performance-debug", action="store_true",
                        help="Log camera processing and LCD transfer times")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.touch_debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.performance_debug:
        logging.getLogger("spectrometer.performance").setLevel(logging.DEBUG)

    from .ui import SpectrometerUI
    from .camera import CameraSettings, CameraStream
    from .config import SettingsStore
    from .server import running_server

    try:
        store = SettingsStore()
        overrides = {}
        if args.fps is not None:
            overrides['frame_rate'] = args.fps
        if args.frame_averaging is not None:
            overrides['frame_averaging'] = args.frame_averaging
        if args.exposure_us is not None or args.auto_exposure:
            overrides['exposure_us'] = args.exposure_us
        store.update(camera=overrides)
        settings = CameraSettings(**store.data['camera'], roi=store.data['calibration']['sensor_area'])
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    camera = None if args.no_camera else CameraStream(settings)
    with running_server(camera=camera, settings_store=store):
        SpectrometerUI(fullscreen=not args.windowed,
                       camera_settings=deepcopy(store.data['camera']),
                       calibration_settings=deepcopy(store.data['calibration']),
                       on_calibration_changed=lambda values: store.update(calibration=values),
                       on_camera_settings_changed=lambda camera_values, calibration=None:
                       store.update(camera=camera_values, calibration=calibration),
                       camera=camera).run()


if __name__ == "__main__":
    main()
