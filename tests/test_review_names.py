from pathlib import Path
import pickle
import tempfile
import unittest

from spectrometer.review import ReviewList


class ReviewNameTests(unittest.TestCase):
    def test_names_load_in_background_with_legacy_and_invalid_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            for second, record in enumerate(({'name': 'Lamp spectrum'}, {}, {'name': ' '})):
                path = Path(directory) / f'spectrum-2026-09-14-12-00-{second:02}.pkl'
                path.write_bytes(pickle.dumps(record))
            broken = Path(directory) / 'spectrum-2026-09-14-12-00-03.pkl'
            broken.write_bytes(b'invalid')
            review = ReviewList(directory)
            try:
                review.refresh()
                changes = 0
                while review._reconcile_queue or review._names_future is not None:
                    review.poll_names()
                    review._names_future.result(timeout=5)
                    changes += bool(review.poll_names())
                self.assertEqual(changes, 4)
                self.assertEqual([review.name(p) for p in review.entries],
                                 ['Unreadable spectrum', 'Unnamed spectrum',
                                  'Unnamed spectrum', 'Lamp spectrum'])
                self.assertIsNone(review._names_future)
            finally:
                review.close()


class RenameTests(unittest.TestCase):
    def test_keyboard_rename_cancel_and_atomic_failure(self):
        from unittest.mock import patch
        from datetime import datetime
        from spectrometer.ui import SpectrometerUI
        from spectrometer.capture import save_capture, rename_capture
        from spectrometer.catalog import SpectrumCatalog
        with tempfile.TemporaryDirectory() as directory:
            record = {'name': 'Original', 'timestamp': datetime(2026, 9, 16, 12),
                      'spectrum_intensity': [1, 2, 3], 'instrument_settings': {'exposure_time_us': 100}}
            path = save_capture(record, directory)
            original = path.read_bytes()
            ui = SpectrometerUI(review_directory=directory)
            try:
                ui._open_review()
                self.assertEqual([b[0] for b in ui.buttons], ['Display', 'Rename', 'Back'])
                ui.buttons[1][2]()
                self.assertEqual(ui.mode, 'review')
                ui.review.selected = 0
                ui.buttons[1][2]()
                ui._rename_future.result(timeout=5)
                ui._poll_capture()
                self.assertEqual(ui._capture_name, 'Original')
                ui._capture_name = 'Cancelled'
                ui.buttons[-1][2]()
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(ui.review.selected, 0)
                ui.buttons[1][2]()
                ui._rename_future.result(timeout=5)
                ui._poll_capture()
                ui._capture_name = ' '
                ui.buttons[-2][2]()
                self.assertEqual(ui._capture_message, 'Enter a name')
                ui._capture_name = 'New name'
                ui.buttons[-2][2]()
                ui._rename_future.result(timeout=5)
                ui._poll_capture()
                self.assertEqual(ui.mode, 'review')
                self.assertEqual(ui.review.name(path), 'New name')
                self.assertEqual(pickle.loads(path.read_bytes()), dict(record, name='New name'))
                catalog = SpectrumCatalog(directory)
                try:
                    self.assertEqual(catalog.list_api()[0]['name'], 'New name')
                finally:
                    catalog.close()
                saved = path.read_bytes()
                with patch('spectrometer.capture.os.replace', side_effect=OSError('Disk full')):
                    with self.assertRaises(OSError):
                        rename_capture(path, 'Failed')
                self.assertEqual(path.read_bytes(), saved)
                self.assertEqual(list(Path(directory).glob('.spectrum-*')), [])
            finally:
                ui.review.close()
                ui.settings_view.close()
