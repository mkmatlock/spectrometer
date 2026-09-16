"""Camera configuration and actual OpenCV processing without Pi hardware."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.camera import BAR_SIZE, CameraSettings, CameraStream, SpectrumFrame, process_frame, spectrum_bar
from spectrometer.ui import SpectrometerUI


class CameraTests(unittest.TestCase):
    def fake_camera(self):
        camera = Mock()
        camera.camera_configuration.return_value = {
            "raw": {"size": (4056, 3040), "format": "SRGGB12_CSI2P"}}
        camera.camera_controls = {"ExposureTime": (114, 1000000, 20000),
                                  "FrameDurationLimits": (100000, 1000000, None)}
        return camera

    def test_full_resolution_mode_and_fps_limit(self):
        camera = self.fake_camera()
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
            with CameraStream(CameraSettings(frame_rate=30)) as stream:
                self.assertIsNone(stream.poll())
                config = camera.create_video_configuration.call_args.kwargs
                self.assertEqual(config["main"], {"size": (4056, 3040), "format": "RGB888"})
                self.assertEqual(config["raw"], {"size": (4056, 3040), "format": "SRGGB12_CSI2P"})
                self.assertEqual(config["sensor"], {"output_size": (4056, 3040), "bit_depth": 12})
                self.assertEqual(config["buffer_count"], 2)
                camera.set_controls.assert_called_once_with(
                    {"FrameDurationLimits": (100000, 100000), "AeEnable": True})
            camera.stop.assert_called_once_with()
            camera.close.assert_called_once_with()

    def test_manual_exposure_extends_frame_period(self):
        camera = self.fake_camera()
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
            with CameraStream(CameraSettings(exposure_us=200000)):
                camera.set_controls.assert_called_once_with({
                    "FrameDurationLimits": (200000, 200000),
                    "AeEnable": False, "ExposureTime": 200000})

    def test_five_fps_default_does_not_enumerate_modes(self):
        from unittest.mock import PropertyMock

        camera = self.fake_camera()
        with patch.object(type(camera), "sensor_modes", create=True,
                          new_callable=PropertyMock) as modes, \
                patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
            modes.side_effect = AssertionError("Mode enumeration must not run")
            with CameraStream():
                camera.set_controls.assert_called_once_with({
                    "FrameDurationLimits": (200000, 200000), "AeEnable": True})

    def test_missing_mode_closes_camera_without_starting(self):
        camera = self.fake_camera()
        camera.camera_configuration.return_value = {
            "raw": {"size": (2028, 1520), "format": "SRGGB12_CSI2P"}}
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
            with self.assertRaisesRegex(RuntimeError, "4056:3040:12:P"):
                with CameraStream():
                    pass
        camera.start.assert_not_called()
        camera.close.assert_called_once_with()

    def test_invalid_settings(self):
        for fps in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CameraSettings(frame_rate=fps)
        with self.assertRaises(ValueError):
            CameraSettings(exposure_us=0)

    def test_packed_mode_accepts_all_bayer_orders(self):
        for order in ("RGGB", "GRBG", "GBRG", "BGGR"):
            with self.subTest(order=order):
                camera = self.fake_camera()
                camera.camera_configuration.return_value["raw"]["format"] = "S%s12_CSI2P" % order
                with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
                    with CameraStream():
                        camera.start.assert_called_once_with(show_preview=False)

    def test_wrong_depth_or_packing_reports_actual_format(self):
        for fmt in ("SBGGR10_CSI2P", "SBGGR12", "BGGR_PISP_COMP1"):
            with self.subTest(format=fmt):
                camera = self.fake_camera()
                camera.camera_configuration.return_value["raw"]["format"] = fmt
                with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
                    with self.assertRaisesRegex(RuntimeError, "actual raw size=.*" + fmt):
                        with CameraStream():
                            pass
                camera.start.assert_not_called()
                camera.close.assert_called_once_with()

    def test_crop_and_colour_order(self):
        frame = np.zeros((3040, 4056, 3), dtype=np.uint8)
        frame[:] = (0, 255, 0)  # Outside the ROI must not appear.
        frame[1550:1800, :1750] = (0, 0, 255)
        frame[1550:1800, 1750:3500] = (255, 0, 0)
        bar = spectrum_bar(frame)
        pixels = np.frombuffer(bar, dtype=np.uint8).reshape(BAR_SIZE[1], BAR_SIZE[0], 3)
        self.assertTrue(np.all(pixels[:, :231] == (255, 0, 0)))
        self.assertTrue(np.all(pixels[:, 231:] == (0, 0, 255)))
        with self.assertRaises(ValueError):
            spectrum_bar(frame[:100])

    def test_latest_frame_only_and_callback_failure(self):
        stream = CameraStream()
        mapped = Mock()
        mapped.__enter__ = Mock(return_value=SimpleNamespace(array="frame"))
        mapped.__exit__ = Mock(return_value=False)
        module = SimpleNamespace(MappedArray=Mock(return_value=mapped))
        with patch.dict(sys.modules, {"picamera2": module}), \
                patch("spectrometer.camera.process_frame", side_effect=[b"first", b"newest", ValueError("bad frame")]):
            stream._on_frame(Mock())
            stream._on_frame(Mock())
            self.assertEqual(stream.poll(), b"newest")
            self.assertIsNone(stream.poll())
            stream._on_frame(Mock())
            with self.assertRaisesRegex(RuntimeError, "processing failed"):
                stream.poll()

    def test_channel_sum_totals_use_full_roi_without_overflow(self):
        frame = np.zeros((3040, 4056, 3), dtype=np.uint8)
        frame[1550:1800, 0] = (255, 255, 255)
        frame[1550:1800, 1] = (0, 0, 255)
        frame[1550:1800, 3499] = (255, 0, 0)
        frame[1800, :] = 255  # Excluded lower edge.
        frame[1550:1800, 3500] = 255  # Excluded right edge.
        result = process_frame(frame)
        self.assertEqual(result.intensity.dtype, np.int32)
        self.assertEqual(result.intensity.shape, (3500,))
        self.assertEqual(result.intensity[0], 3 * 63750)
        self.assertEqual(result.intensity[1], 255 * 250)
        self.assertEqual(result.intensity[3499], 255 * 250)
        self.assertTrue(np.all(result.intensity[2:3499] == 0))

    def test_ui_renders_bar_without_changing_plot_placeholder(self):
        camera = Mock()
        camera.poll.side_effect = [SpectrumFrame(
            bytes((255, 0, 0)) * (BAR_SIZE[0] * BAR_SIZE[1]), np.zeros(3500, dtype=np.int32)), None]
        ui = SpectrometerUI(camera=camera)
        pygame.font.init()
        try:
            self.assertTrue(ui._poll_camera())
            surface = pygame.Surface((480, 320))
            ui.draw(surface, pygame.font.Font(None, 22))
            self.assertEqual(surface.get_at((10, 218))[:3], (255, 0, 0))
            self.assertNotEqual(surface.get_at((10, 10))[:3], (255, 0, 0))
            self.assertFalse(ui._poll_camera())
        finally:
            pygame.font.quit()


if __name__ == "__main__":
    unittest.main()
