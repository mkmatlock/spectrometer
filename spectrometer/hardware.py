"""Bridge the supplied SPI LCD and I2C touchscreen drivers to the UI."""

from contextlib import ExitStack


class LCDBackend:
    """Own the hardware resources; importing this module needs no Pi libraries."""

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

    def present(self, surface):
        import pygame
        from PIL import Image

        image = Image.frombytes("RGB", surface.get_size(),
                                pygame.image.tostring(surface, "RGB"))
        self.display.show_image(image)

    def read_touch(self):
        self.touch.read_touch_data()
        count, coordinates = self.touch.get_touch_xy()
        if not count:
            return None
        # The driver already mirrors native X (319 - raw_x). Swap its axes
        # to match the LCD driver's landscape MADCTL setting (0x78).
        point = coordinates[0]
        return (max(0, min(479, point["y"])),
                max(0, min(319, point["x"])))
