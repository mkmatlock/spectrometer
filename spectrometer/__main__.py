"""Command-line entry point; hardware libraries are not required for --help."""

import argparse
import logging


def main():
    parser = argparse.ArgumentParser(description="Spectrometer UI on the SPI LCD and I2C touchscreen")
    parser.add_argument("--windowed", action="store_true", help="Open a desktop preview")
    parser.add_argument("--touch-debug", action="store_true",
                        help="Log raw/mapped touch coordinates and button actions")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.touch_debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .ui import SpectrometerUI

    SpectrometerUI(fullscreen=not args.windowed).run()


if __name__ == "__main__":
    main()
