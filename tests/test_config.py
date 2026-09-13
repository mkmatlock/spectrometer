from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectrometer.config import DEFAULTS, SettingsStore
from spectrometer.camera import process_frame
from spectrometer.review import load_spectrum
from spectrometer.ui import SpectrometerUI


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / '.spectrometer_config'

    def test_defaults_and_round_trip(self):
        store = SettingsStore(self.path)
        self.assertEqual(pickle.loads(self.path.read_bytes()), DEFAULTS)
        store.update(camera={'frame_rate': 4, 'exposure_us': 20000,
                             'resolution': (2028, 1520)},
                     calibration={'scale': {100: 532.1}, 'sensor_area': (0, 100, 2000, 200)})
        self.assertEqual(SettingsStore(self.path).data, store.data)
        self.assertEqual(DEFAULTS['calibration']['scale'], {})

    def test_failed_write_preserves_previous_file_and_memory(self):
        store = SettingsStore(self.path)
        original = self.path.read_bytes()
        with patch('spectrometer.config.os.replace', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                store.update(camera={'frame_rate': 3})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(store.data, DEFAULTS)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_settings_and_corrupt_file_are_not_overwritten(self):
        store = SettingsStore(self.path)
        original = self.path.read_bytes()
        for changes in ({'frame_rate': 0}, {'exposure_us': -1}, {'resolution': None}):
            with self.assertRaises(ValueError):
                store.update(camera=changes)
        self.assertEqual(self.path.read_bytes(), original)
        self.path.write_bytes(b'broken pickle')
        with self.assertRaises(ValueError):
            SettingsStore(self.path)
        self.assertEqual(self.path.read_bytes(), b'broken pickle')

    def test_label_accept_modify_delete_persist_and_failed_save_keeps_label(self):
        store = SettingsStore(self.path)
        ui = SpectrometerUI(calibration_settings=deepcopy(store.data['calibration']),
                            on_calibration_changed=lambda data: store.update(calibration=data))
        self.addCleanup(ui.review.close)
        self.addCleanup(ui.settings_view.close)
        values = np.zeros(3500, np.int32)
        values[1000] = 50000
        ui.mode = 'saved'
        ui._scale_active = True
        ui._reset_peaks(values)
        for label in ('532.1', '532.2'):
            ui._select_peak(tuple(ui._peaks.positions[0]))
            ui._label_peak()
            ui._label_input = label
            ui._accept_label()
            self.assertEqual(SettingsStore(self.path).data['calibration']['scale'], {1000: float(label)})
        ui._select_peak(tuple(ui._peaks.positions[0]))
        with patch.object(store, '_write', side_effect=OSError('Disk full')):
            with self.assertLogs('spectrometer.ui', level='ERROR'):
                ui._delete_peak_label()
        self.assertEqual(ui.calibration_settings['scale'], {1000: 532.2})
        ui._delete_peak_label()
        self.assertEqual(SettingsStore(self.path).data['calibration']['scale'], {})

    def test_custom_sensor_area_capture_review_and_peak_coordinates(self):
        roi = (100, 20, 600, 40)
        image = np.zeros((480, 640, 3), np.uint8)
        image[20:40, 200] = 255
        frame = process_frame(image, roi, (640, 480))
        self.assertEqual(frame.roi, roi)
        self.assertEqual(frame.intensity.shape, (500,))
        self.assertEqual(frame.intensity[100], 20 * 255)
        capture = self.path.parent / 'spectrum.pkl'
        capture.write_bytes(pickle.dumps({'spectrum_roi': roi, 'spectrum_bar': frame.bar,
                                         'spectrum_intensity': frame.intensity}))
        loaded = load_spectrum(capture)
        ui = SpectrometerUI()
        self.addCleanup(ui.review.close)
        self.addCleanup(ui.settings_view.close)
        ui._update_plot(loaded)
        ui._reset_peaks(loaded.intensity)
        self.assertEqual(ui._plot.maximum, 20 * 255)
        self.assertEqual(ui._peaks.select(ui._peaks.positions[0]), 200)
