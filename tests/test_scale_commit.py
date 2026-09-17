from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from spectrometer import capture
from spectrometer.config import SettingsStore
from spectrometer.review import ReviewList, load_spectrum
from tests.spectrum_fixtures import spectrum_record


class ScaleCommitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = capture.save_capture(spectrum_record(), self.directory)
        self.original = self.path.read_bytes()
        self.store = SettingsStore(self.directory / '.spectrometer_config')
        self.before = deepcopy(self.store.data['calibration'])
        self.after = deepcopy(self.before)
        self.labels = {200: 650.0, 400: 450.0}
        self.after['scale'] = deepcopy(self.labels)
        self.settings_bytes = self.store.path.read_bytes()

    def save_settings(self, values):
        self.store.update(calibration=values)

    def commit(self, **overrides):
        options = dict(save_settings=self.save_settings,
                       settings_before=self.before, settings_after=self.after)
        options.update(overrides)
        return capture.update_scale_calibration(self.path, self.labels, **options)

    def assert_unchanged(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.store.path.read_bytes(), self.settings_bytes)
        self.assertEqual(self.store.data['calibration'], self.before)
        self.assertEqual(list(self.directory.glob('.spectrum-*')), [])

    def test_commit_prepares_capture_then_saves_both_settings_and_scale(self):
        def save(values):
            prepared = list(self.directory.glob('.spectrum-*'))
            self.assertEqual(len(prepared), 1)
            record = pickle.loads(prepared[0].read_bytes())
            self.assertEqual(record['instrument_settings']['calibration_settings']['scale'],
                             self.labels)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.save_settings(values)

        self.assertEqual(self.commit(save_settings=save), self.labels)
        self.assertEqual(load_spectrum(self.path).calibration['scale'], self.labels)
        self.assertEqual(SettingsStore(self.store.path).data['calibration'], self.after)
        self.assertEqual(self.store.data['calibration'], self.after)
        self.assertEqual(list(self.directory.glob('.spectrum-*')), [])

    def test_serialization_failure_does_not_call_settings_callback(self):
        save = Mock(side_effect=self.save_settings)
        with patch('spectrometer.capture.pickle.dump', side_effect=OSError('Disk full')):
            with self.assertRaisesRegex(OSError, 'Disk full'):
                self.commit(save_settings=save)
        save.assert_not_called()
        self.assert_unchanged()

    def test_settings_failure_preserves_capture_and_config(self):
        replace = capture.os.replace

        def fail_config(source, destination):
            if Path(destination) == self.store.path:
                raise OSError('Config write failed')
            return replace(source, destination)

        with patch('spectrometer.capture.os.replace', side_effect=fail_config):
            with self.assertRaisesRegex(OSError, 'Config write failed'):
                self.commit()
        self.assert_unchanged()

    def test_capture_publication_failure_restores_settings(self):
        replace = capture.os.replace
        save = Mock(side_effect=self.save_settings)

        def fail_capture(source, destination):
            if Path(destination) == self.path:
                self.assertEqual(self.store.data['calibration'], self.after)
                raise OSError('Capture publication failed')
            return replace(source, destination)

        with patch('spectrometer.capture.os.replace', side_effect=fail_capture):
            with self.assertRaisesRegex(OSError, 'Capture publication failed'):
                self.commit(save_settings=save)
        self.assertEqual([call.args[0] for call in save.call_args_list],
                         [self.after, self.before])
        self.assert_unchanged()

    def test_rollback_failure_reports_both_failures(self):
        replace = capture.os.replace

        def save(values):
            if values == self.before:
                raise OSError('Config restoration failed')
            self.save_settings(values)

        def fail_capture(source, destination):
            if Path(destination) == self.path:
                raise OSError('Capture publication failed')
            return replace(source, destination)

        with patch('spectrometer.capture.os.replace', side_effect=fail_capture):
            with self.assertRaisesRegex(RuntimeError,
                                        'Capture publication failed.*Config restoration failed'):
                self.commit(save_settings=save)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.store.data['calibration'], self.after)
        self.assertEqual(SettingsStore(self.store.path).data['calibration'], self.after)
        self.assertEqual(list(self.directory.glob('.spectrum-*')), [])

    def test_callback_requires_snapshots_without_changing_either_file(self):
        for option in ('settings_before', 'settings_after'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.commit(**{option: None})
        self.assert_unchanged()

    def test_review_worker_snapshots_path_and_settings_and_runs_callback_off_thread(self):
        review = ReviewList(self.directory)
        review.loaded_path = self.path
        entered, release = threading.Event(), threading.Event()
        observed = []
        main_thread = threading.get_ident()
        labels, before, after = (deepcopy(value)
                                 for value in (self.labels, self.before, self.after))

        def block():
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError('Test did not release save worker')

        def save(values):
            observed.append((threading.get_ident(), deepcopy(values)))
            self.save_settings(values)

        try:
            review._names_executor.submit(block)
            self.assertTrue(entered.wait(timeout=5))
            future = review.save_scale(labels, save_settings=save,
                                       settings_before=before, settings_after=after)
            self.assertFalse(future.done())
            review.loaded_path = self.directory / 'other.pkl'
            labels.clear()
            before['scale'][0] = 99
            after['scale'].clear()
            release.set()
            self.assertEqual(future.result(timeout=5), self.labels)
            self.assertEqual(load_spectrum(self.path).calibration['scale'], self.labels)
            self.assertEqual(observed[0][1], self.after)
            self.assertNotEqual(observed[0][0], main_thread)
            self.assertFalse(review.loaded_path.exists())
        finally:
            release.set()
            review.close()

    def test_concurrent_rename_waits_for_settings_commit_and_preserves_both_changes(self):
        entered, release, contending = (threading.Event() for _ in range(3))
        lock = capture._MUTATION_LOCK

        class ObservedLock:
            def __enter__(self):
                if entered.is_set():
                    contending.set()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        def save(values):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError('Test did not release settings callback')
            self.save_settings(values)

        with patch('spectrometer.capture._MUTATION_LOCK', ObservedLock()), \
                ThreadPoolExecutor(max_workers=2) as workers:
            saving = workers.submit(self.commit, save_settings=save)
            try:
                self.assertTrue(entered.wait(timeout=5))
                renaming = workers.submit(capture.rename_capture, self.path, 'Renamed spectrum')
                self.assertTrue(contending.wait(timeout=5))
                self.assertFalse(renaming.done())
            finally:
                release.set()
            saving.result(timeout=5)
            renaming.result(timeout=5)
        record = pickle.loads(self.path.read_bytes())
        self.assertEqual(record['name'], 'Renamed spectrum')
        self.assertEqual(record['instrument_settings']['calibration_settings']['scale'], self.labels)
        self.assertEqual(self.store.data['calibration'], self.after)


if __name__ == '__main__':
    unittest.main()
