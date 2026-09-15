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
