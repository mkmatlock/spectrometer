import tempfile
from pathlib import Path
import pickle
import unittest

import numpy as np

from spectrometer.camera import SpectrumFrame
from spectrometer.ui import SpectrometerUI
from spectrometer.config import DEFAULTS
from tests.spectrum_fixtures import spectrum_record


class ReviewPeakTests(unittest.TestCase):
    def test_review_touch_opens_label_dialog_and_filter_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            values = np.zeros(3500, dtype=np.int32)
            values[900], values[2400] = 50000, 40000
            bar = bytes(462 * 38 * 3)
            data = pickle.dumps(spectrum_record(spectrum_intensity=values, spectrum_bar=bar))
            path = Path(directory) / 'spectrum-2026-09-13-12-00-00.pkl'
            path.write_bytes(data)
            ui = SpectrometerUI(review_directory=directory)
            try:
                ui._open_review()
                ui.review.selected = 0
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                buttons = ui.buttons
                for index, pixel in enumerate((900, 2400)):
                    position = tuple(ui._peaks.positions[index])
                    ui._pointer_event('lcd', position, True)
                    ui._pointer_event('lcd', position, False)
                    self.assertEqual(ui._review_peak_label, f'Pixel {pixel}')
                    self.assertTrue(ui._peak_dialog)
                    self.assertEqual([b[0] for b in ui.buttons], ['Label', 'Cancel'])
                ui.buttons[-1][2]()
                self.assertIsNone(ui._peaks.marker)
                self.assertIs(ui.buttons, buttons)
                ui._ask_filter()
                position = tuple(ui._peaks.positions[0])
                ui._pointer_event('lcd', position, True)
                ui._pointer_event('lcd', position, False)
                self.assertIsNone(ui._review_peak_label)
                filtered = np.zeros(3500, dtype=np.int32)
                filtered[1800] = 30000
                ui._apply_filter(SpectrumFrame(bar, filtered, calibration=DEFAULTS['calibration']))
                self.assertIsNone(ui._review_peak_label)
                np.testing.assert_array_equal(ui._peaks.indices, [1800])
                ui._apply_filter(SpectrumFrame(bar, np.zeros(3500, dtype=np.int32),
                                               calibration=DEFAULTS['calibration']))
                ui._select_peak((100, 100))
                self.assertEqual(ui._review_peak_label, 'No peaks found')
                self.assertFalse(ui._peak_dialog)
                self.assertEqual(path.read_bytes(), data)
            finally:
                ui.review.close()
                ui.settings_view.close()
