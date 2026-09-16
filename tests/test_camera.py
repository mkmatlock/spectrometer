"""Camera configuration and actual OpenCV processing without Pi hardware."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.camera import (BAR_SIZE, CameraSettings, CameraStream, FrameAverager,
                                 SpectrumFrame, process_frame, spectrum_bar)
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

    def test_runtime_reconfigure_restarts_with_new_settings(self):
        camera = self.fake_camera()
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: camera)}):
            with CameraStream() as stream:
                settings = CameraSettings(frame_rate=10, exposure_us=50000,
                                          frame_averaging=2)
                stream.request_reconfigure(settings).result(timeout=2)
                self.assertEqual(stream.settings, settings)
                self.assertEqual(camera.create_video_configuration.call_count, 2)
                self.assertEqual(camera.configure.call_count, 2)
                self.assertEqual(camera.start.call_count, 2)
                camera.stop.assert_called_once_with()
                self.assertEqual(stream._averager.count, 2)

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
        for averaging in (0, 11, 1.5):
            with self.assertRaises(ValueError):
                CameraSettings(frame_averaging=averaging)

    def test_rolling_frame_average(self):
        averager = FrameAverager(3)
        results = []
        for value in (3, 6, 12, 24):
            frame = SpectrumFrame(bytes([value]) * (BAR_SIZE[0] * BAR_SIZE[1] * 3),
                                  np.array([value, value * 2], np.int32))
            results.append(averager.add(frame, np.full((2, 2, 3), value, np.uint8)))
        self.assertIsNone(results[0])
        self.assertIsNone(results[1])
        np.testing.assert_array_equal(results[2].intensity, [7, 14])
        np.testing.assert_array_equal(results[3].intensity, [14, 28])
        self.assertEqual(results[3].bar[0], 14)
        np.testing.assert_array_equal(averager.image(), np.full((2, 2, 3), 14, np.uint8))

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

    def test_sensor_preview_bypasses_averaging_and_blocks_capture(self):
        stream = CameraStream(CameraSettings(resolution=(640, 480), roi=(0, 0, 640, 20)))
        self.addCleanup(stream.close)
        stream._sensor_preview_enabled = True
        stream._started = True
        stream._camera = Mock()
        pixels = np.full((480, 640, 3), (10, 20, 30), np.uint8)
        mapped = Mock()
        mapped.__enter__ = Mock(return_value=SimpleNamespace(array=pixels))
        mapped.__exit__ = Mock(return_value=False)
        with patch.dict(sys.modules, {'picamera2': SimpleNamespace(MappedArray=lambda *a, **k: mapped)}), \
                patch('spectrometer.camera.process_frame') as process:
            stream._on_frame(Mock())
            process.assert_not_called()
        preview, size, resolution = stream.poll_sensor_preview()
        self.assertEqual(tuple(preview[:3]), (30, 20, 10))
        self.assertEqual(size, (341, 256))
        self.assertEqual(resolution, (640, 480))
        self.assertIsNone(stream.poll_sensor_preview())
        self.assertFalse(stream.request_capture())
        self.assertIsNone(stream.request_api_capture('test'))
        stream.request_end_sensor_preview((0, 10, 640, 30)).result(timeout=2)
        self.assertFalse(stream._sensor_preview_enabled)
        self.assertFalse(stream._started)
        self.assertEqual(stream.settings.roi, (0, 10, 640, 30))

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
        stream = CameraStream(CameraSettings(resolution=(4, 2), roi=(0, 0, 4, 2),
                                             frame_averaging=1))
        mapped = Mock()
        mapped.__enter__ = Mock(return_value=SimpleNamespace(array=np.zeros((2, 4, 3), np.uint8)))
        mapped.__exit__ = Mock(return_value=False)
        module = SimpleNamespace(MappedArray=Mock(return_value=mapped))
        with patch.dict(sys.modules, {"picamera2": module}), \
                patch("spectrometer.camera.process_frame", side_effect=[b"first", b"newest", ValueError("bad frame")]), \
                patch.object(stream._averager, 'add', side_effect=lambda frame, image: frame):
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
