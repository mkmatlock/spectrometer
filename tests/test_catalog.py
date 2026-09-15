from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

from spectrometer.capture import save_capture
from spectrometer.catalog import DuplicateSpectrumID, SpectrumCatalog
from spectrometer.review import ReviewList


def record(second=0, name='Lamp'):
    return {'timestamp': datetime(2026, 9, 14, 12, 0, second, tzinfo=timezone.utc),
            'name': name, 'raw_camera_output': bytes(128),
            'spectrum_intensity': [1, 2, 3]}


class CatalogTests(unittest.TestCase):
    def test_capture_is_indexed_immediately_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(record(name='  Lamp  '), directory)
            catalog = SpectrumCatalog(directory)
            with patch('spectrometer.catalog.pickle.load',
                       side_effect=AssertionError('warm lookup unpickled a capture')):
                catalog.reconcile_all(force=True)
                self.assertEqual(catalog.list_api()[0]['name'], 'Lamp')
                self.assertEqual(catalog.find(1789387200000), path)

    def test_index_failure_does_not_turn_a_saved_capture_into_a_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('spectrometer.catalog.SpectrumCatalog', side_effect=OSError('index full')), \
                self.assertLogs('spectrometer.capture', level='ERROR'):
            path = save_capture(record(), directory)
            self.assertTrue(path.exists())

    def test_legacy_backfill_then_unchanged_and_corrupt_files_are_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / 'spectrum-2026-09-14-12-00-00.pkl'
            broken = Path(directory) / 'spectrum-2026-09-14-12-00-01.pkl'
            valid.write_bytes(pickle.dumps({'name': 'Legacy'}))
            broken.write_bytes(b'broken')
            catalog = SpectrumCatalog(directory)
            original_load = pickle.load
            with patch('spectrometer.catalog.pickle.load', wraps=original_load) as load:
                with self.assertLogs('spectrometer.catalog', level='ERROR'):
                    catalog.reconcile_all(force=True)
                self.assertEqual(load.call_count, 2)
                catalog.reconcile_all(force=True)
                self.assertEqual(load.call_count, 2)
            rows = catalog.row_map()
            self.assertEqual(rows[valid.name]['name'], 'Legacy')
            self.assertFalse(rows[broken.name]['readable'])

    def test_changed_and_deleted_files_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(record(name='First'), directory)
            catalog = SpectrumCatalog(directory)
            path.write_bytes(pickle.dumps(record(name='Changed')))
            catalog.reconcile_all(force=True)
            self.assertEqual(catalog.list_api()[0]['name'], 'Changed')
            path.unlink()
            catalog.reconcile_all(force=True)
            self.assertEqual(catalog.list_api(), [])

    def test_duplicate_ids_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ('a', 'b'):
                path = Path(directory) / f'spectrum-{suffix}.pkl'
                path.write_bytes(pickle.dumps(record()))
            catalog = SpectrumCatalog(directory)
            catalog.reconcile_all(force=True)
            with self.assertRaises(DuplicateSpectrumID):
                catalog.find(1789387200000)

    def test_corrupt_database_rebuilds_from_pickles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-14-12-00-00.pkl'
            path.write_bytes(pickle.dumps(record()))
            (Path(directory) / '.spectrometer_index.sqlite3').write_bytes(b'not sqlite')
            with self.assertLogs('spectrometer.catalog', level='ERROR'):
                catalog = SpectrumCatalog(directory)
            catalog.reconcile_all(force=True)
            self.assertEqual(len(catalog.list_api()), 1)
            self.assertTrue((Path(directory) / '.spectrometer_index.sqlite3.corrupt').exists())

    def test_review_uses_warm_index_without_opening_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            save_capture(record(name='Indexed'), directory)
            with patch('spectrometer.review.pickle.load',
                       side_effect=AssertionError('review unpickled indexed metadata')):
                review = ReviewList(directory)
                try:
                    review.refresh()
                    self.assertEqual(review.name(review.entries[0]), 'Indexed')
                    review.poll_names()
                    self.assertIsNone(review._names_future)
                finally:
                    review.close()

    def test_concurrent_reconciliation_and_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            for second in range(8):
                save_capture(record(second, str(second)), directory)
            catalog = SpectrumCatalog(directory)
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(
                    lambda index: (catalog.reconcile_all(force=True), catalog.list_api())[1],
                    range(16)))
            self.assertTrue(all(len(result) == 8 for result in results))


if __name__ == '__main__':
    unittest.main()
