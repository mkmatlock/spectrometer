from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.camera import CameraStream
from spectrometer.config import SettingsStore
from spectrometer.sensor import load_sensor
from spectrometer.review import raw_rgb
from spectrometer.ui import SpectrometerUI


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'spectrum-2026-09-13-12-00-00.pkl'
        # Packed RGGB high bytes with distinguishable red/green/blue planes.
        raw = np.zeros((480, 960), np.uint8)
        raw[::2, ::3], raw[::2, 1::3] = 200, 80
        raw[1::2, ::3], raw[1::2, 1::3] = 80, 20
        self.path.write_bytes(pickle.dumps({'raw_camera_output': raw,
            'instrument_settings': {'raw_camera_format': {
                'size': (640, 480), 'format': 'SRGGB12_CSI2P', 'stride': 960}}}))
        self.original = self.path.read_bytes()
        self.store = SettingsStore(Path(self.temp.name) / '.spectrometer_config')
        self.store.update(camera={'resolution': (640, 480)},
                          calibration={'sensor_area': (0, 100, 600, 200)})
        self.camera = Mock()
        self.camera.poll_sensor_preview.return_value = load_sensor(self.path)
        self.ui = SpectrometerUI(review_directory=self.temp.name, camera=self.camera,
            camera_settings=deepcopy(self.store.data['camera']),
            calibration_settings=deepcopy(self.store.data['calibration']),
            on_calibration_changed=lambda data: self.store.update(calibration=data))
        self.addCleanup(self.ui.review.close)
        self.addCleanup(self.ui.settings_view.close)

    def open_sensor(self):
        with patch('spectrometer.settings.network_status', return_value=('Unavailable', 'Unavailable')):
            self.ui._open_settings()
            self.ui.settings_view.future.result(timeout=5)
        self.ui._calibrate()
        self.ui._calibration_selected = 1
        self.ui._select_calibration()
        self.assertEqual(self.ui.mode, 'sensor')
        self.assertTrue(self.ui._poll_camera())
        self.assertEqual([b[0] for b in self.ui.buttons], ['Accept', 'Pause', 'Cancel'])
        rect = self.ui._sensor.rect
        self.ui._pointer_event('lcd', rect.center, True)
        self.ui._pointer_motion('lcd', rect.topleft)
        self.ui._pointer_event('lcd', rect.topleft, False)
        self.assertEqual(self.ui._sensor.roi, (0, 100, 600, 200))
        self.ui._toggle_sensor_pause()
        self.assertEqual(self.ui.buttons[1][0], 'Resume')
        self.assertFalse(self.ui._poll_camera())
        self.assertEqual(self.ui._sensor.roi, (0, 100, 600, 200))

    def drag(self):
        rect = self.ui._sensor.rect
        start = (rect.left, rect.top + 32)
        end = (rect.right, rect.top + 128)
        self.ui._pointer_event('lcd', start, True)
        self.ui._pointer_motion('lcd', end)
        self.ui._pointer_event('lcd', end, False)
        self.assertEqual(self.ui._sensor.roi, (0, 60, 640, 240))

    def test_full_preview_color_and_cancel_keeps_settings_and_capture(self):
        pixels, size, resolution, bounds = load_sensor(self.path)
        self.assertEqual(resolution, (640, 480))
        self.assertEqual(bounds, (0, 0, 640, 480))
        self.assertEqual(tuple(np.frombuffer(pixels, np.uint8)[:3]), (200, 80, 20))
        self.assertLessEqual(size[1], 256)
        self.open_sensor()
        self.drag()
        self.ui.buttons[1][2]()
        self.assertFalse(self.ui._sensor_paused)
        self.assertEqual(self.ui._sensor.roi, (0, 60, 640, 240))
        pygame.font.init()
        self.addCleanup(pygame.font.quit)
        self.ui.draw(pygame.Surface((480, 320)), pygame.font.Font(None, 22))
        self.ui.buttons[2][2]()
        self.assertEqual(self.ui.mode, 'settings')
        self.assertEqual(SettingsStore(self.store.path).data['calibration']['sensor_area'], (0, 100, 600, 200))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_averaged_roi_capture_remains_available_to_review_and_sensor(self):
        roi = (100, 20, 600, 40)
        bgr = np.empty((20, 500, 3), np.uint8)
        bgr[:] = (10, 20, 30)
        record = {
            'spectrum_roi': roi,
            'averaged_camera_output': bgr,
            'instrument_settings': {
                'raw_camera_format': {'size': (640, 480), 'format': 'SRGGB12_CSI2P'},
                'averaged_camera_format': {
                    'size': (500, 20), 'format': 'BGR888', 'origin': (100, 20),
                    'sensor_size': (640, 480)}}}
        path = Path(self.temp.name) / 'spectrum-2026-09-13-12-00-01.pkl'
        path.write_bytes(pickle.dumps(record))
        rgb = raw_rgb(record)
        self.assertEqual(tuple(rgb[0, 0]), (30, 20, 10))
        pixels, size, resolution, bounds = load_sensor(path)
        self.assertEqual(resolution, (640, 480))
        self.assertEqual(bounds, roi)
        self.assertEqual(tuple(np.frombuffer(pixels, np.uint8)[:3]), (30, 20, 10))

    def test_accept_persists_and_updates_camera(self):
        self.open_sensor()
        self.drag()
        self.ui.buttons[0][2]()
        self.assertEqual(self.ui.mode, 'settings')
        self.camera.request_end_sensor_preview.assert_called_once_with((0, 60, 640, 240))
        self.assertEqual(SettingsStore(self.store.path).data['calibration']['sensor_area'], (0, 60, 640, 240))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_invalid_roi_and_failed_save_stay_in_editor(self):
        self.open_sensor()
        self.ui._sensor.roi = (0, 0, 10, 10)
        with self.assertLogs('spectrometer.ui', level='ERROR'):
            self.ui._accept_sensor()
        self.assertEqual(self.ui.mode, 'sensor')
        self.drag()
        with patch.object(self.store, '_write', side_effect=OSError('Disk full')):
            with self.assertLogs('spectrometer.ui', level='ERROR'):
                self.ui._accept_sensor()
        self.assertEqual(self.ui.mode, 'sensor')
        self.assertEqual(self.ui.calibration_settings['sensor_area'], (0, 100, 600, 200))

    def test_drag_reverse_direction_clamps_and_ignores_second_pointer(self):
        self.open_sensor()
        rect = self.ui._sensor.rect
        self.ui._pointer_event('lcd', (rect.right - 1, rect.bottom - 1), True)
        self.ui._pointer_motion('other', (0, 0))
        self.assertEqual(self.ui._sensor.roi, (0, 100, 600, 200))
        self.ui._pointer_motion('lcd', (-100, -100))
        self.ui._pointer_event('lcd', (-100, -100), False)
        self.assertEqual(self.ui._sensor.roi, (2, 0, 640, 478))
