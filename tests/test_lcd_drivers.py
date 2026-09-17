"""Driver regression checks without attached GPIO, SPI, or I2C hardware."""

import importlib
import sys
import unittest
from unittest.mock import Mock, patch

import numpy


class DriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        modules = {name: Mock() for name in
                   ("spidev", "gpiozero", "smbus", "RPi", "RPi.GPIO")}
        modules["gpiozero"].__all__ = []
        with patch.dict(sys.modules, modules):
            cls.lcd_class = importlib.import_module("spectrometer.st7796").st7796
            cls.touch_class = importlib.import_module("spectrometer.ft6336u").ft6336u

    def make_lcd(self):
        lcd = self.lcd_class.__new__(self.lcd_class)
        lcd.width, lcd.height, lcd.np = 320, 480, numpy
        lcd._orientation = None
        lcd._window_x = lcd._window_y = None
        lcd._packed_buffers = {}
        lcd.command, lcd.data, lcd.set_windows = Mock(), Mock(), Mock()
        lcd.digital_write, lcd.GPIO_DC_PIN, lcd.SPI = Mock(), Mock(), Mock()
        return lcd

    def test_frame_bounds_and_single_rgb565_transfer(self):
        lcd = self.make_lcd()
        image = numpy.full((320, 480, 3), (255, 0, 0), numpy.uint8)
        image[0, 0], image[0, -1] = (0, 255, 0), (0, 0, 255)
        pixels = lcd.prepare_array(480, 320, image, mirror=True)
        lcd.write_prepared(0, 0, 480, 320, pixels, full=True)
        lcd.set_windows.assert_called_once_with(0, 0, 479, 319)
        lcd.SPI.writebytes2.assert_called_once()
        data = bytes(lcd.SPI.writebytes2.call_args.args[0])
        self.assertEqual(len(data), 480 * 320 * 2)
        self.assertEqual(data[:2], b'\x00\x1f')
        self.assertEqual(data[2 * 479:2 * 480], b'\x07\xe0')
        self.assertEqual(data[2 * 480:], b'\xf8\x00' * (480 * 319))

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
        lcd = self.make_lcd()
        image = numpy.array([[(255, 0, 0), (0, 0, 255)]], numpy.uint8)
        pixels = lcd.prepare_array(2, 1, image, mirror=True)
        lcd.write_prepared(10, 217, 2, 1, pixels)
        lcd.set_windows.assert_called_once_with(468, 217, 469, 217)
        self.assertEqual(bytes(lcd.SPI.writebytes2.call_args.args[0]), b'\x00\x1f\xf8\x00')
        with self.assertRaises(ValueError):
            lcd.write_prepared(10, 217, 2, 1, b'')

    def test_strided_rgb_view_can_be_packed_without_a_copy(self):
        lcd = self.make_lcd()
        source = numpy.zeros((4, 3, 3), numpy.uint8)
        source[1, 0], source[1, 2] = (255, 0, 0), (0, 0, 255)
        view = source[1:2, ::2]
        pixels = lcd.prepare_array(2, 1, view, mirror=True)
        lcd.write_prepared(10, 217, 2, 1, pixels)
        self.assertEqual(bytes(lcd.SPI.writebytes2.call_args.args[0]), b'\x00\x1f\xf8\x00')

    def test_window_coordinates_are_batched_and_cached(self):
        lcd = self.lcd_class.__new__(self.lcd_class)
        lcd.GPIO_DC_PIN = Mock()
        lcd.digital_write, lcd.spi_writebyte = Mock(), Mock()
        lcd._window_x = lcd._window_y = None
        lcd.set_windows(9, 36, 470, 165)
        self.assertEqual(lcd.spi_writebyte.call_count, 5)
        lcd.set_windows(9, 36, 470, 165)
        self.assertEqual(lcd.spi_writebyte.call_count, 6)  # RAM write command only.
        lcd.set_windows(9, 217, 470, 254)
        self.assertEqual(lcd.spi_writebyte.call_count, 9)  # New Y plus RAM write.

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
