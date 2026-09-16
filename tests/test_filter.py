from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectrometer.camera import BAR_SIZE
from spectrometer.review import load_channels
from spectrometer.ui import SpectrometerUI


class FilterTests(unittest.TestCase):
    def test_channel_totals_and_coloured_bar(self):
        rgb = np.empty((250, 3500, 3), dtype=np.uint8)
        rgb[:] = (10, 20, 30)
        rgb[:, 42, 0] = 255
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-test.pkl'
            path.write_bytes(pickle.dumps({}))
            original = path.read_bytes()
            with patch('spectrometer.review.raw_rgb', return_value=rgb):
                channels = load_channels(path)
            self.assertEqual(path.read_bytes(), original)
        for index, name in enumerate(('Red', 'Green', 'Blue')):
            frame = channels[name]
            np.testing.assert_array_equal(frame.intensity, rgb[:, :, index].sum(axis=0))
            bar = np.frombuffer(frame.bar, np.uint8).reshape(38, 462, 3)
            self.assertTrue(np.all(bar[:, :, [i for i in range(3) if i != index]] == 0))
        self.assertEqual(channels['Red'].intensity[42], 63750)

    def test_filter_modal_restores_all_and_never_changes_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-13-12-00-00.pkl'
            original_bar = bytes([50]) * (BAR_SIZE[0] * BAR_SIZE[1] * 3)
            original = pickle.dumps({'spectrum_intensity': np.full(3500, 1000, np.int32),
                                     'spectrum_bar': original_bar})
            path.write_bytes(original)
            ui = SpectrometerUI(review_directory=directory)
            try:
                ui._open_review()
                ui.review.selected = 0
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                points = ui._plot.points
                ui._ask_filter()
                self.assertEqual([b[0] for b in ui.buttons],
                                 ['Red', 'Green', 'Blue', 'Emission', 'Black Body', 'Back'])
                rgb = np.full((250, 3500, 3), 80, np.uint8)
                with patch('spectrometer.review.raw_rgb', return_value=rgb):
                    ui._choose_filter('Green')
                    ui._choose_filter('Blue')
                    ui.review.filter_future.result(timeout=5)
                    ui._poll_review()
                self.assertTrue(ui._filter_dialog)
                self.assertEqual(ui.review.filter_channel, ('Red',))
                self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (80, 0, 0))
                ui._choose_filter('Red')
                self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (0, 0, 0))
                self.assertEqual(len(ui._peaks.indices), 0)
                ui._choose_filter('Red')
                ui._ask_filter()
                ui._choose_filter('Green')
                ui._choose_filter('Blue')
                self.assertEqual(ui._plot.points, points)
                self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (50, 50, 50))
                self.assertEqual(path.read_bytes(), original)
                ui._back_filter()
                self.assertEqual([b[0] for b in ui.buttons], ['Delete', 'Modes', 'Back'])
            finally:
                ui.review.close()
                ui.settings_view.close()
