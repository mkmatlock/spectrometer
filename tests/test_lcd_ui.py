"""Test the direct hardware path with fake devices and no SDL display."""

import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pygame

from spectrometer.hardware import LCDBackend
from spectrometer.ui import SpectrometerUI


class LCDUITests(unittest.TestCase):
    def test_display_is_closed_if_touch_initialization_fails(self):
        display = Mock()
        modules = {
            "spectrometer.st7796": SimpleNamespace(st7796=Mock(return_value=display)),
            "spectrometer.ft6336u": SimpleNamespace(
                ft6336u=Mock(side_effect=OSError("I2C unavailable"))),
        }
        with patch.dict(sys.modules, modules):
            with self.assertRaises(OSError):
                with LCDBackend():
                    self.fail("Failed initialization must not enter the context")
        display.close.assert_called_once_with()

    def test_landscape_touch_mapping(self):
        backend = LCDBackend()
        backend.touch = Mock()
        for portrait, landscape in (((319, 0), (479, 0)),
                                    ((0, 479), (0, 319)),
                                    ((39, 399), (80, 280))):
            backend.touch.get_touch_xy.return_value = (
                1, [{"x": portrait[0], "y": portrait[1]}])
            self.assertEqual(backend.read_touch(), landscape)
        backend.touch.get_touch_xy.return_value = (0, [])
        self.assertIsNone(backend.read_touch())

    def test_touch_maps_to_each_button(self):
        callbacks = [Mock(), Mock(), Mock()]
        ui = SpectrometerUI(on_capture=callbacks[0], on_review=callbacks[1],
                            on_settings=callbacks[2])
        backend = LCDBackend()
        backend.touch = Mock()
        # Actual Pi samples, touching Capture, Review, then Settings.
        samples = [((302, 398), (81, 302)),
                   ((293, 248), (231, 293)),
                   ((290, 89), (390, 290))]
        for index, ((raw_x, raw_y), expected) in enumerate(samples):
            # The low-level driver returns mirrored X, not raw controller X.
            backend.touch.get_touch_xy.return_value = (
                1, [{"x": 319 - raw_x, "y": raw_y}])
            position = backend.read_touch()
            self.assertEqual(position, expected)
            self.assertEqual(ui._button_at(position), index)
            ui._pointer_event("lcd", position, True)
            ui._pointer_event("lcd", position, False)
        for callback in callbacks:
            callback.assert_called_once_with()

    def test_invalid_touch_is_not_clamped_to_screen(self):
        backend = LCDBackend()
        backend.touch = Mock()
        backend.touch.get_touch_xy.return_value = (1, [{"x": 288, "y": 500}])
        self.assertIsNone(backend.read_touch())

    def test_surface_conversion(self):
        backend = LCDBackend()
        backend.display = Mock()
        surface = pygame.Surface((480, 320))
        surface.fill((255, 0, 0))
        backend.present(surface)
        image = backend.display.show_image.call_args.args[0]
        self.assertEqual(image.size, (480, 320))
        self.assertEqual(image.mode, "RGB")
        self.assertEqual(image.getpixel((479, 319)), (255, 0, 0))

    def test_hardware_loop_press_hold_release_and_cleanup(self):
        callback = Mock()
        ui = SpectrometerUI(on_capture=callback)
        hardware = Mock()
        hardware.read_touch.side_effect = [(80, 280), (80, 280), None, None]
        stopping = Mock()
        stopping.is_set.side_effect = [False, False, False, False, True]
        with patch("spectrometer.hardware.LCDBackend") as factory, \
                patch("spectrometer.ui.threading.Event", return_value=stopping), \
                patch.object(pygame.display, "init") as display_init:
            factory.return_value.__enter__.return_value = hardware
            ui.run()
            callback.assert_called_once_with()
            self.assertEqual(hardware.present.call_count, 3)
            factory.return_value.__exit__.assert_called_once()
            display_init.assert_not_called()
        self.assertFalse(pygame.font.get_init())

    def test_release_outside_does_not_activate(self):
        callback = Mock()
        ui = SpectrometerUI(on_capture=callback)
        ui._pointer_event("lcd", (80, 280), True)
        ui._pointer_event("lcd", (80, 50), False)
        callback.assert_not_called()
        self.assertIsNone(ui._pressed)

    def test_camera_refresh_transfers_only_bar(self):
        ui = SpectrometerUI()
        hardware = Mock()
        hardware.read_touch.return_value = None
        stopping = Mock()
        stopping.is_set.side_effect = [False, True]
        with patch("spectrometer.hardware.LCDBackend") as factory, \
                patch("spectrometer.ui.threading.Event", return_value=stopping), \
                patch.object(ui, "_poll_camera", return_value=True):
            factory.return_value.__enter__.return_value = hardware
            ui.run()
        self.assertEqual(hardware.present.call_count, 2)
        regions = hardware.present.call_args.args[1]
        self.assertEqual(regions, [pygame.Rect(60, 36, 400, 130),
                                   pygame.Rect(9, 217, 462, 38)])


if __name__ == "__main__":
    unittest.main()
