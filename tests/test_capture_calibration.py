import pickle
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectrometer.review import load_channels, record_calibration
from spectrometer.ui import SpectrometerUI


class CaptureCalibrationTests(unittest.TestCase):
    def test_calibration_layout_compatibility_and_nested_precedence(self):
        old = {'scale': {100: 700, 200: 500}}
        self.assertEqual(record_calibration({'calibration_settings': old}), old)
        record = {'calibration_settings': old,
                  'instrument_settings': {'calibration_settings': {'scale': {}}}}
        self.assertEqual(record_calibration(record), {'scale': {}})
        self.assertEqual(record_calibration({}), {})


    def test_review_uses_saved_scale_filters_preserve_it_and_legacy_uses_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-13-12-00-00.pkl'
            values = np.zeros(3500, np.int32)
            values[1500] = 50000
            record = {'spectrum_intensity': values, 'spectrum_bar': bytes(462 * 38 * 3),
                      'calibration_settings': {'scale': {1000: 700, 2000: 500},
                                               'sensor_area': (0, 1550, 3500, 1800)}}
            record['instrument_settings'] = {'calibration_settings': record.pop('calibration_settings')}
            path.write_bytes(pickle.dumps(record))
            original = path.read_bytes()
            ui = SpectrometerUI(review_directory=directory,
                                calibration_settings={'scale': {1000: 900, 2000: 800}})
            try:
                ui._open_review()
                ui.review.selected = 0
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                ui._select_peak(ui._peaks.positions[0])
                self.assertEqual(ui._review_peak_label, '600.0 nm')
                self.assertEqual(ui.calibration_settings['scale'][1000], 900)
                with patch('spectrometer.review.raw_rgb', return_value=np.zeros((250, 3500, 3), np.uint8)):
                    channels = load_channels(path)
                for frame in channels.values():
                    ui._apply_filter(frame)
                    self.assertEqual(ui._plot.scale.wavelength(1500), 600)
                self.assertEqual(path.read_bytes(), original)
                ui._scale_active = True
                ui._update_plot(ui.review._channel_cache['All'])
                self.assertIsNone(ui._plot.scale)
                ui._scale_active = False
                del record['instrument_settings']['calibration_settings']
                path.write_bytes(pickle.dumps(record))
                ui._review_list()
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                self.assertIsNone(ui._plot.scale)
                ui._select_peak(ui._peaks.positions[0])
                self.assertEqual(ui._review_peak_label, 'Pixel 1500')
                ui._exit_review()
                self.assertEqual(ui._plot.scale.wavelength(1500), 850)
            finally:
                ui.review.close()
                ui.settings_view.close()
