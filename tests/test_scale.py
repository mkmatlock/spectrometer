import pickle
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pygame

from spectrometer.scale import PeakSelection, peak_indices
from spectrometer.ui import SpectrometerUI


class ScaleTests(unittest.TestCase):
    def test_plateaus_and_flat_spectra(self):
        np.testing.assert_array_equal(peak_indices([0, 2, 2, 2, 0, 3, 0]), [2, 5])
        self.assertEqual(len(peak_indices(np.zeros(3500))), 0)
        self.assertEqual(len(peak_indices(np.arange(3500))), 0)

    def test_nearest_peak_uses_screen_distance(self):
        values = np.zeros(3500)
        values[1000], values[2500] = 50000, 30000
        peaks = PeakSelection(values, pygame.Rect(9, 36, 462, 130), 63750)
        self.assertEqual(peaks.select(peaks.positions[1] + (2, 4)), 2500)
        self.assertEqual(peaks.marker, tuple(np.rint(peaks.positions[1]).astype(int)))

    def test_scale_capture_selection_peak_dialog_and_return(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-13-12-00-00.pkl'
            values = np.zeros(3500, dtype=np.int32)
            values[1000], values[2500] = 50000, 30000
            original = pickle.dumps({'spectrum_intensity': values,
                                     'spectrum_bar': bytes(462 * 38 * 3)})
            path.write_bytes(original)
            camera = Mock()
            camera.settings_snapshot.return_value = {}
            ui = SpectrometerUI(camera=camera, review_directory=directory)
            try:
                with patch('spectrometer.settings.network_status', return_value=('Unavailable', 'Unavailable')):
                    ui._open_settings()
                    ui.settings_view.future.result(timeout=5)
                ui._calibrate()
                ui._calibration_selected = 2
                ui._select_calibration()
                self.assertEqual(ui.mode, 'review')
                self.assertTrue(ui._scale_active)
                self.assertFalse(ui._calibration_dialog)
                ui.review.selected = 0
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual([b[0] for b in ui.buttons], ['Back'])
                position = tuple(ui._peaks.positions[0] + (2, 2))
                ui._pointer_event('lcd', position, True)
                ui._pointer_event('lcd', position, False)
                self.assertTrue(ui._peak_dialog)
                self.assertIn('1000', ui._peak_message)
                self.assertEqual([b[0] for b in ui.buttons], ['Label', 'Back'])
                ui._label_peak()
                self.assertTrue(ui._keypad_open)
                ui._cancel_label()
                ui._back_peak()
                self.assertIsNone(ui._peaks.marker)
                self.assertEqual(ui.mode, 'saved')
                self.assertFalse(ui._peak_dialog)
                ui._review_list()
                ui._exit_review()
                self.assertEqual(ui.mode, 'settings')
                self.assertFalse(ui._scale_active)
                camera.resume.assert_not_called()
                self.assertEqual(path.read_bytes(), original)
            finally:
                ui.review.close()
                ui.settings_view.close()
