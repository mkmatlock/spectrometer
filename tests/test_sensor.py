from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.config import SettingsStore
from spectrometer.ui import SpectrometerUI


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'spectrum-2026-09-13-12-00-00.pkl'
        self.path.write_bytes(b'Untouched capture')
        self.original = self.path.read_bytes()
        self.preview = (bytes((200, 80, 20)) * (341 * 256),
                        (341, 256), (640, 480))
        self.store = SettingsStore(Path(self.temp.name) / '.spectrometer_config')
        self.store.update(camera={'resolution': (640, 480)},
                          calibration={'sensor_area': (0, 100, 600, 200)})
        self.camera = Mock()
        self.camera.poll_sensor_preview.return_value = self.preview
        self.ui = SpectrometerUI(review_directory=self.temp.name, camera=self.camera,
            camera_settings=deepcopy(self.store.data['camera']),
            calibration_settings=deepcopy(self.store.data['calibration']),
            on_calibration_changed=lambda data: self.store.update(calibration=data))
        self.camera.set_calibration.reset_mock()
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
        self.assertEqual([b[0] for b in self.ui.buttons], ['Accept', 'Reset', 'Pause', 'Cancel'])
        self.ui.buttons[1][2]()
        self.assertIsNone(self.ui._sensor)
        self.assertFalse(self.ui._sensor_paused)
        self.assertTrue(self.ui._poll_camera())
        rect = self.ui._sensor.rect
        self.ui._pointer_event('lcd', rect.center, True)
        self.ui._pointer_motion('lcd', rect.topleft)
        self.ui._pointer_event('lcd', rect.topleft, False)
        self.assertEqual(self.ui._sensor.roi, (0, 100, 600, 200))
        self.ui._toggle_sensor_pause()
        self.assertEqual(self.ui.buttons[2][0], 'Resume')
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
        pixels, size, resolution = self.preview
        self.assertEqual(resolution, (640, 480))
        self.assertEqual(tuple(np.frombuffer(pixels, np.uint8)[:3]), (200, 80, 20))
        self.assertLessEqual(size[1], 256)
        self.open_sensor()
        self.drag()
        self.ui.buttons[2][2]()
        self.assertFalse(self.ui._sensor_paused)
        self.assertEqual(self.ui._sensor.roi, (0, 60, 640, 240))
        self.ui.buttons[1][2]()
        self.assertTrue(self.ui._sensor_paused)
        self.assertEqual(self.ui.buttons[2][0], 'Resume')
        self.assertEqual(self.ui._sensor.roi, (0, 0, 640, 480))
        self.assertFalse(self.ui._poll_camera())
        self.assertEqual(self.ui.calibration_settings['sensor_area'], (0, 100, 600, 200))
        self.camera.set_calibration.assert_not_called()
        self.camera.request_end_sensor_preview.assert_not_called()
        pygame.font.init()
        self.addCleanup(pygame.font.quit)
        self.ui.draw(pygame.Surface((480, 320)), pygame.font.Font(None, 22))
        self.ui.buttons[3][2]()
        self.assertEqual(self.ui.mode, 'settings')
        self.camera.request_end_sensor_preview.assert_called_once_with((0, 100, 600, 200))
        self.assertEqual(SettingsStore(self.store.path).data['calibration']['sensor_area'], (0, 100, 600, 200))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_accept_persists_full_current_resolution_after_reset(self):
        self.open_sensor()
        self.drag()
        self.ui._sensor.start = (10, 20)
        self.ui._sensor.message = 'ROI must fit image'
        pause_count = self.camera.request_pause.call_count
        self.ui.buttons[1][2]()
        self.assertTrue(self.ui._sensor_paused)
        self.assertEqual(self.camera.request_pause.call_count, pause_count)
        self.assertEqual(self.ui._sensor.roi, (0, 0, 640, 480))
        self.assertIsNone(self.ui._sensor.start)
        self.assertEqual(self.ui._sensor.message, '')
        self.assertEqual(SettingsStore(self.store.path).data['calibration']['sensor_area'], (0, 100, 600, 200))
        self.ui.buttons[0][2]()
        self.assertEqual(self.ui.mode, 'settings')
        self.camera.request_end_sensor_preview.assert_called_once_with((0, 0, 640, 480))
        self.camera.set_calibration.assert_called_once_with(self.ui.calibration_settings)
        self.assertEqual(SettingsStore(self.store.path).data['calibration']['sensor_area'], (0, 0, 640, 480))
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
