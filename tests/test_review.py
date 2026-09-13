from datetime import datetime, timezone
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import pygame

from spectrometer.camera import BAR_SIZE
from spectrometer.capture import save_capture
from spectrometer.review import ReviewList, load_spectrum, raw_bar
from spectrometer.ui import SpectrometerUI


def record(second=0):
    return {'timestamp': datetime(2026, 9, 12, 12, 0, second, tzinfo=timezone.utc),
            'spectrum_intensity': np.arange(3500, dtype=np.int32),
            'spectrum_bar': bytes((255, 0, 0)) * (BAR_SIZE[0] * BAR_SIZE[1])}


class ReviewTests(unittest.TestCase):
    def test_newest_first_scroll_and_empty_list(self):
        with tempfile.TemporaryDirectory() as directory:
            review = ReviewList(directory)
            try:
                review.refresh()
                self.assertEqual(review.entries, [])
                review.display()
                self.assertEqual(review.message, 'Select a capture first')
                for second in range(8):
                    save_capture(record(second), directory)
                review.refresh()
                self.assertTrue(review.entries[0].name.endswith('07.pkl'))
                review.scroll(100)
                self.assertEqual(review.offset, 3)
                review.scroll(-100)
                self.assertEqual(review.offset, 0)
            finally:
                review.close()

    def test_select_display_back_exit_and_live_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            save_capture(record(), directory)
            camera = Mock()
            ui = SpectrometerUI(camera=camera, review_directory=directory)
            try:
                original = ui._plot
                ui._open_review()
                camera.pause.assert_called_once()
                self.assertFalse(ui._poll_camera())
                camera.poll.assert_not_called()
                ui._pointer_event('lcd', (50, 50), True)
                ui._pointer_event('lcd', (50, 50), False)
                ui.buttons[0][2]()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual([b[0] for b in ui.buttons], ['Back'])
                self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (255, 0, 0))
                ui.buttons[0][2]()
                self.assertEqual(ui.mode, 'review')
                self.assertEqual(ui.review.selected, 0)
                ui.buttons[1][2]()
                self.assertEqual(ui.mode, 'live')
                self.assertIs(ui._plot, original)
                camera.resume.assert_called_once()
            finally:
                ui.review.close()

    def test_swipe_scroll_does_not_select(self):
        ui = SpectrometerUI()
        try:
            ui._open_review()
            ui.review.entries = [Path(str(i)) for i in range(12)]
            ui._pointer_event('lcd', (50, 220), True)
            ui._pointer_motion('lcd', (50, 90))
            ui._pointer_event('lcd', (50, 90), False)
            self.assertEqual(ui.review.offset, 3)
            self.assertIsNone(ui.review.selected)
        finally:
            ui.review.close()

    def test_bad_pickle_shows_error_and_stays_in_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-broken.pkl'
            path.write_bytes(b'broken')
            review = ReviewList(directory)
            try:
                review.refresh()
                review.selected = 0
                review.display()
                try:
                    review.future.result(timeout=5)
                except Exception:
                    pass
                self.assertIsNone(review.poll())
                self.assertTrue(review.message.startswith('Cannot load:'))
            finally:
                review.close()

    def test_legacy_bayer_bar_with_stride_padding(self):
        # Neutral white produces white regardless of Bayer order; row padding
        # must never be interpreted as sensor pixels.
        raw = np.zeros((3040, 6112), dtype=np.uint8)
        raw[:, :6084] = 255
        config = {'size': (4056, 3040), 'stride': 6112, 'format': 'SBGGR12_CSI2P'}
        for settings in ({'raw_camera_format': config},
                         {'instrument_settings': {'raw_camera_format': config}}):
            result = raw_bar(dict(settings, raw_camera_output=raw))
            self.assertEqual(result, bytes([255]) * (462 * 38 * 3))

    def test_invalid_spectrum_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data = record()
            data['spectrum_intensity'] = np.zeros(10)
            path = save_capture(data, directory)
            with self.assertRaises(ValueError):
                load_spectrum(path)
