"""Bridge the supplied SPI LCD and I2C touchscreen drivers to the UI."""

from contextlib import ExitStack
import logging
import time


LOGGER = logging.getLogger(__name__)


class LCDBackend:
    """Own the hardware resources; importing this module needs no Pi libraries."""

    def __init__(self):
        self._last_touch = None

    def __enter__(self):
        from .st7796 import st7796
        from .ft6336u import ft6336u

        self._resources = ExitStack()
        try:
            self.display = st7796()
            self._resources.callback(self.display.close)
            self.touch = ft6336u()
            self._resources.callback(self.touch.close)
        except BaseException:
            self._resources.close()
            raise
        return self

    def __exit__(self, *exc):
        return self._resources.__exit__(*exc)

    def present(self, surface, regions=None):
        import pygame
        from PIL import Image

        started = time.monotonic()
        patches = [surface.get_rect()] if regions is None else regions
        pixels = 0
        for rect in patches:
            patch = surface.subsurface(rect)
            image = Image.frombytes("RGB", patch.get_size(),
                                    pygame.image.tostring(patch, "RGB"))
            if regions is None:
                self.display.show_image(image)
            else:
                self.display.show_region(rect.x, rect.y, image)
            pixels += rect.width * rect.height
        LOGGER.debug("LCD transfer %s pixels in %.1f ms", pixels,
                     (time.monotonic() - started) * 1000)

    def read_touch(self):
        self.touch.read_touch_data()
        count, coordinates = self.touch.get_touch_xy()
        if not count:
            if self._last_touch is not None:
                LOGGER.debug("Touch released")
            self._last_touch = None
            return None
        # Undo the driver's native X mirror before using it as landscape Y.
        # Physical button samples confirm UI = (479 - raw_y, raw_x).
        point = coordinates[0]
        position = (479 - point["y"], 319 - point["x"])
        sample = (count, point["x"], point["y"])
        if sample != self._last_touch:
            LOGGER.debug("Touch count=%s raw=(%s, %s) UI=%s", count,
                         319 - point["x"], point["y"], position)
        self._last_touch = sample
        # Do not turn an invalid sample into a press on a screen edge.
        if not (0 <= position[0] < 480 and 0 <= position[1] < 320):
            return None
        return position
