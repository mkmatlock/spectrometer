from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.camera import CameraSettings, SpectrumFrame, process_frame
from spectrometer.channel import ChannelCalibration
from spectrometer.config import SettingsStore
from spectrometer.ui import SpectrometerUI


class ChannelCalibrationTests(unittest.TestCase):
    def test_channel_sum_and_ranges(self):
        frame = np.empty((2, 4, 3), np.uint8)
        frame[:] = (1, 10, 100)  # BGR
        result = process_frame(frame, (0, 0, 4, 2), (4, 2), channel_ranges={
            'Blue': (0, 0), 'Green': (1, 1), 'Red': (2, 3)})
        np.testing.assert_array_equal(result.intensity, [2, 20, 200, 200])
        self.assertEqual(result.maximum, 2 * 255 * 3)

    def test_dual_thumbs_reset_and_accept_persist(self):
        pygame.font.init()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-15-12-00-00.pkl'
            path.write_bytes(__import__('pickle').dumps({'name': 'Calibration source'}))
            store = SettingsStore(Path(directory) / '.spectrometer_config')
            camera = Mock()
            camera.settings = CameraSettings()
            ui = SpectrometerUI(
                camera=camera, review_directory=directory,
                calibration_settings=deepcopy(store.data['calibration']),
                on_calibration_changed=lambda data: store.update(calibration=data))
            values = np.arange(3500, dtype=np.int32)
            frames = {name: SpectrumFrame(b'', values, maximum=250 * 255)
                      for name in ('Red', 'Green', 'Blue')}
            try:
                ui._settings_buttons = []
                ui._live_view = (ui._plot, ui._camera_bar)
                ui._channel_active = True
                ui.review.refresh()
                ui._review_list()
                ui.review.selected = 0
                with patch('spectrometer.review.load_channels', return_value=frames):
                    ui._display_capture()
                    ui.review.future.result(timeout=5)
                ui._poll_review()
                self.assertEqual(ui.mode, 'channel')
                self.assertEqual([button[0] for button in ui.buttons],
                                 ['Accept', 'Reset', 'Cancel'])

                editor = ui._channel
                low_x = editor._thumb_x(editor.ranges['Red'][0])
                ui._pointer_event('lcd', (low_x, editor.ROWS['Red']), True)
                ui._pointer_motion('lcd', (editor.TRACK.centerx, editor.ROWS['Red']))
                ui._pointer_event('lcd', (editor.TRACK.centerx, editor.ROWS['Red']), False)
                self.assertGreater(editor.ranges['Red'][0], 0)
                changed = editor.ranges['Red']

                ui._accept_channel()
                self.assertEqual(store.data['calibration']['channel_ranges']['Red'], changed)
                self.assertEqual(camera.set_calibration.call_count, 2)
                self.assertEqual(ui.mode, 'settings')
            finally:
                ui.review.close()
                ui.settings_view.close()
        pygame.font.quit()

    def test_reset_restores_full_roi_without_saving(self):
        frames = {name: SpectrumFrame(b'', np.zeros(3500, np.int32))
                  for name in ('Red', 'Green', 'Blue')}
        editor = ChannelCalibration(frames, {'Red': (500, 2500)})
        self.assertEqual(editor.ranges['Red'], (500, 2500))
        editor.reset()
        self.assertEqual(editor.ranges, editor.full_ranges())
