"""Driver regression checks without attached GPIO, SPI, or I2C hardware."""

import importlib
import sys
import unittest
from unittest.mock import Mock, patch

import numpy
from PIL import Image


class DriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        modules = {name: Mock() for name in
                   ("spidev", "gpiozero", "smbus", "RPi", "RPi.GPIO")}
        modules["gpiozero"].__all__ = []
        with patch.dict(sys.modules, modules):
            cls.lcd_class = importlib.import_module("spectrometer.st7796").st7796
            cls.touch_class = importlib.import_module("spectrometer.ft6336u").ft6336u

    def test_frame_bounds_and_single_rgb565_transfer(self):
        for size in ((480, 320), (320, 480)):
            with self.subTest(size=size):
                lcd = self.lcd_class.__new__(self.lcd_class)
                lcd.width, lcd.height, lcd.np = 320, 480, numpy
                lcd.command = Mock()
                lcd.data = Mock()
                lcd.set_windows = Mock()
                lcd.digital_write = Mock()
                lcd.GPIO_DC_PIN = Mock()
                lcd.SPI = Mock()
                image = Image.new("RGB", size, "red")
                # Asymmetric corner markers detect a reflection or rotation.
                image.putpixel((0, 0), (0, 255, 0))
                image.putpixel((size[0] - 1, 0), (0, 0, 255))
                lcd.show_image(image)
                lcd.set_windows.assert_called_once_with(
                    0, 0, size[0] - 1, size[1] - 1, int(size[0] == 480))
                lcd.SPI.writebytes2.assert_called_once()
                data = lcd.SPI.writebytes2.call_args.args[0]
                self.assertIsInstance(data, bytes)
                self.assertEqual(len(data), 480 * 320 * 2)
                left = b'\x00\x1f' if size[0] == 480 else b'\x07\xe0'
                right = b'\x07\xe0' if size[0] == 480 else b'\x00\x1f'
                self.assertEqual(data[:2], left)
                self.assertEqual(data[2 * (size[0] - 1):2 * size[0]], right)
                self.assertEqual(data[2 * size[0]:], b'\xf8\x00' * (size[0] * (size[1] - 1)))

    def test_touch_release_clears_state_and_invalid_count_is_ignored(self):
        touch = self.touch_class.__new__(self.touch_class)
        touch.coordinates = [{"x": 0, "y": 0} for _ in range(2)]
        touch.point_count = 0
        touch.read_bytes = Mock(side_effect=[
            [1, 0, 10, 0, 20, 0, 0] + [0] * 6,
            [0] * 13, [15] + [0] * 12])
        touch.read_touch_data()
        self.assertEqual(touch.coordinates[0], {"x": 309, "y": 20})
        self.assertEqual(touch.point_count, 1)
        touch.read_touch_data()
        self.assertEqual(touch.get_touch_xy(), (0, []))
        touch.read_touch_data()
        self.assertEqual(touch.get_touch_xy(), (0, []))
        self.assertEqual(touch.read_bytes.call_count, 3)
        touch.read_bytes.assert_called_with(0x02, 13)

    def test_landscape_region_position_and_pixel_order(self):
        lcd = self.lcd_class.__new__(self.lcd_class)
        lcd.width, lcd.height, lcd.np = 320, 480, numpy
        lcd.command, lcd.data, lcd.set_windows = Mock(), Mock(), Mock()
        lcd.digital_write, lcd.GPIO_DC_PIN, lcd.SPI = Mock(), Mock(), Mock()
        patch = Image.new("RGB", (2, 1), "red")
        patch.putpixel((1, 0), (0, 0, 255))
        lcd.show_region(10, 217, patch)
        lcd.set_windows.assert_called_once_with(468, 217, 469, 217, 1)
        lcd.SPI.writebytes2.assert_called_once_with(b'\x00\x1f\xf8\x00')
        with self.assertRaises(ValueError):
            lcd.show_region(479, 0, patch)

    def test_touch_event_flags_and_short_read(self):
        touch = self.touch_class.__new__(self.touch_class)
        touch.coordinates = [{"x": 0, "y": 0} for _ in range(2)]
        for flag, count in ((0, 1), (2, 1), (1, 0), (3, 0)):
            touch.read_bytes = Mock(return_value=[
                1, flag << 6, 31, 0x11, 141, 0, 0] + [0] * 6)
            touch.read_touch_data()
            self.assertEqual(touch.point_count, count)
            if count:
                self.assertEqual(touch.coordinates[0], {"x": 288, "y": 397})
        touch.read_bytes = Mock(return_value=[1])
        touch.read_touch_data()
        self.assertEqual(touch.get_touch_xy(), (0, []))


if __name__ == "__main__":
    unittest.main()
