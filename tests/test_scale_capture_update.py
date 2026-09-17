from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from spectrometer import capture
from spectrometer.catalog import SpectrumCatalog
from spectrometer.config import DEFAULTS, SettingsStore
from spectrometer.review import ReviewList, load_spectrum
from spectrometer.ui import SpectrometerUI
from tests.spectrum_fixtures import spectrum_record


class ScaleCaptureUpdateTests(unittest.TestCase):
    def make_record(self):
        values = np.zeros(500, np.int32)
        values[250] = 500
        record = spectrum_record(spectrum_roi=(100, 200, 600, 202),
                                 spectrum_intensity=values)
        calibration = record['instrument_settings']['calibration_settings']
        calibration.update(scale={200: 700.0}, channel_ranges={'Red': (120, 500)},
                           extra={'values': [1, 2]})
        record['averaged_camera_output'][:] = (10, 20, 30)
        return record

    def make_ui(self, directory, path, **kwargs):
        ui = SpectrometerUI(review_directory=directory, **kwargs)
        self.addCleanup(ui.review.close)
        self.addCleanup(ui.settings_view.close)
        ui._open_review()
        ui._scale_active = True
        ui.review.selected = ui.review.entries.index(path)
        ui._display_capture()
        ui.review.future.result(timeout=5)
        ui._poll_review()
        self.assertEqual(ui.mode, 'saved')
        self.assertIsNone(ui._plot.scale)
        return ui

    def run_during_scale_write(self, path, concurrent_change):
        writing, release, contending = (threading.Event() for _ in range(3))
        lock = capture._MUTATION_LOCK
        dump = pickle.dump

        class ObservedLock:
            def __enter__(self):
                if writing.is_set():
                    contending.set()
                return lock.__enter__()

            def __exit__(self, *args):
                return lock.__exit__(*args)

        def paused_dump(*args, **kwargs):
            if not writing.is_set():
                writing.set()
                if not release.wait(timeout=5):
                    raise TimeoutError('Test did not release scale writer')
            return dump(*args, **kwargs)

        with patch('spectrometer.capture._MUTATION_LOCK', ObservedLock()), \
                patch('spectrometer.capture.pickle.dump', side_effect=paused_dump), \
                ThreadPoolExecutor(max_workers=2) as workers:
            saving = workers.submit(capture.update_scale_calibration, path,
                                    {200: 700.0, 500: 400.0})
            try:
                self.assertTrue(writing.wait(timeout=5))
                changing = workers.submit(concurrent_change)
                self.assertTrue(contending.wait(timeout=5))
                self.assertFalse(changing.done())
            finally:
                release.set()
            saving.result(timeout=5)
            changing.result(timeout=5)

    def test_update_preserves_other_data_and_capture_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_record()
            path = capture.save_capture(original, directory)
            other = deepcopy(original)
            other['timestamp'] += timedelta(seconds=1)
            other_path = capture.save_capture(other, directory)
            other_bytes = other_path.read_bytes()
            labels = {200: 700.0, 500: 400.0}
            capture.update_scale_calibration(path, labels)
            updated = pickle.loads(path.read_bytes())
            expected = deepcopy(original)
            expected['instrument_settings']['calibration_settings']['scale'] = labels
            self.assertEqual(set(updated), set(expected))
            for key, value in expected.items():
                if isinstance(value, np.ndarray):
                    np.testing.assert_array_equal(updated[key], value)
                    self.assertEqual(updated[key].dtype, value.dtype)
                else:
                    self.assertEqual(updated[key], value)
            self.assertEqual(other_path.read_bytes(), other_bytes)
            self.assertEqual(load_spectrum(path).calibration['scale'], labels)
            catalog = SpectrumCatalog(directory)
            try:
                row = catalog.row_map()[path.name]
                self.assertEqual(row['mtime_ns'], path.stat().st_mtime_ns)
                self.assertEqual(row['size'], path.stat().st_size)
            finally:
                catalog.close()

    def test_failed_rewrite_preserves_original_and_cleans_temporary_file(self):
        for operation in ('pickle.dump', 'os.replace'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                path = capture.save_capture(self.make_record(), directory)
                original = path.read_bytes()
                with patch('spectrometer.capture.' + operation,
                           side_effect=OSError('Disk full')):
                    with self.assertRaises(OSError):
                        capture.update_scale_calibration(path, {200: 700.0, 500: 400.0})
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob('.spectrum-*')), [])

    def test_overlapping_rename_preserves_new_name_and_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            self.run_during_scale_write(path, lambda: capture.rename_capture(path, 'New name'))
            record = pickle.loads(path.read_bytes())
            self.assertEqual(record['name'], 'New name')
            self.assertEqual(record['instrument_settings']['calibration_settings']['scale'],
                             {200: 700.0, 500: 400.0})

    def test_overlapping_delete_does_not_resurrect_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            self.run_during_scale_write(path, lambda: capture.delete_capture(path))
            self.assertFalse(path.exists())
            with self.assertRaises(FileNotFoundError):
                capture.update_scale_calibration(path, {200: 700.0})
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).glob('.spectrum-*')), [])

    def test_review_save_uses_loaded_capture_and_snapshots_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            review = ReviewList(directory)
            entered, release = threading.Event(), threading.Event()
            path = Path(directory) / 'spectrum-selected.pkl'
            review.loaded_path = path
            review.entries = [Path(directory) / 'spectrum-other.pkl']
            review.selected = 0
            labels = {200: 700.0, 500: 400.0}
            observed = []

            def save(target, values):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError('Test did not release save worker')
                observed.append((target, deepcopy(values)))

            try:
                with patch('spectrometer.capture.update_scale_calibration', side_effect=save):
                    future = review.save_scale(labels)
                    self.assertTrue(entered.wait(timeout=5))
                    labels.clear()
                    release.set()
                    future.result(timeout=5)
                self.assertEqual(observed, [(path, {200: 700.0, 500: 400.0})])
            finally:
                release.set()
                review.close()

    def test_review_rejects_save_without_loaded_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            review = ReviewList(directory)
            try:
                with self.assertRaises(ValueError):
                    review.save_scale({200: 700.0, 500: 400.0})
                self.assertEqual(list(Path(directory).glob('spectrum-*.pkl')), [])
            finally:
                review.close()

    def test_accept_waits_for_save_and_returns_to_list_on_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            ui = self.make_ui(directory, path)
            self.assertEqual([label for label, _, _ in ui.buttons],
                             ['Accept', 'Reset', 'Modes', 'Back'])
            self.assertEqual(ui.buttons[0][2], ui._finish_scale)
            self.assertEqual(ui.buttons[-1][2], ui._cancel_scale)
            self.assertEqual([rect.left for _, rect, _ in ui.buttons],
                             sorted(rect.left for _, rect, _ in ui.buttons))
            original = deepcopy(ui.calibration_settings)
            ui._update_scale(500, 400.0)
            future = Future()
            with patch.object(ui.review, 'save_scale', return_value=future) as save:
                ui._finish_scale()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual(ui.buttons, [])
                self.assertTrue(ui._scale_save_message)
                with patch.object(ui, '_select_peak') as select:
                    position = tuple(ui._peaks.positions[0])
                    ui._pointer_event('lcd', position, True)
                    ui._pointer_event('lcd', position, False)
                    select.assert_not_called()
                ui._finish_scale()
                ui._poll_review()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual(ui.calibration_settings, original)
                save.assert_called_once()
                future.set_result(None)
                ui._poll_review()
            self.assertEqual(ui.mode, 'review')
            self.assertEqual(ui.calibration_settings['scale'], {500: 400.0})
            self.assertEqual([label for label, _, _ in ui.buttons], ['Display', 'Rename', 'Back'])

    def test_failed_save_keeps_scale_graph_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            ui = self.make_ui(directory, path)
            first, retry = Future(), Future()
            with patch.object(ui.review, 'save_scale', side_effect=[first, retry]) as save:
                ui._finish_scale()
                first.set_exception(OSError('Disk full'))
                with self.assertLogs('spectrometer.ui', level='ERROR'):
                    ui._poll_review()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual([label for label, _, _ in ui.buttons], ['Retry', 'Cancel'])
                self.assertTrue(ui._scale_save_message)
                self.assertEqual(ui.buttons[0][2], ui._finish_scale)
                position = ui.buttons[0][1].center
                ui._pointer_event('lcd', position, True)
                ui._pointer_event('lcd', position, False)
                retry.set_result(None)
                ui._poll_review()
                self.assertEqual(save.call_count, 2)
                self.assertEqual(ui.mode, 'review')

    def test_cancel_after_failed_save_leaves_capture_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            original = path.read_bytes()
            ui = self.make_ui(directory, path)
            original_settings = deepcopy(ui.calibration_settings)
            ui._update_scale(500, 400.0)
            failed = Future()
            failed.set_exception(OSError('Read-only file'))
            with patch.object(ui.review, 'save_scale', return_value=failed) as save:
                ui._finish_scale()
                with self.assertLogs('spectrometer.ui', level='ERROR'):
                    ui._poll_review()
                self.assertEqual(ui.buttons[1][0], 'Cancel')
                self.assertEqual(ui.buttons[1][2], ui._cancel_scale_save)
                position = ui.buttons[1][1].center
                ui._pointer_event('lcd', position, True)
                ui._pointer_event('lcd', position, False)
                save.assert_called_once()
            self.assertEqual(ui.mode, 'review')
            self.assertFalse(ui._scale_save_message)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(ui.calibration_settings, original_settings)

    def test_completed_add_modify_delete_and_reset_are_used_by_review(self):
        for operation in ('modify', 'delete', 'reset'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                path = capture.save_capture(self.make_record(), directory)
                ui = self.make_ui(directory, path)
                self.assertTrue(ui._update_scale(200, 700.0))
                self.assertTrue(ui._update_scale(500, 450.0))
                if operation == 'modify':
                    self.assertTrue(ui._update_scale(500, 400.0))
                elif operation == 'delete':
                    self.assertTrue(ui._update_scale(500, None))
                else:
                    ui._reset_scale()
                    ui._confirm_scale_reset()
                expected = deepcopy(ui._scale_labels)
                submitted = []
                original_save = ui.review.save_scale

                def save(labels, **kwargs):
                    future = original_save(labels, **kwargs)
                    submitted.append(future)
                    return future

                with patch.object(ui.review, 'save_scale', side_effect=save):
                    ui._finish_scale()
                    submitted[0].result(timeout=5)
                    ui._poll_review()
                self.assertEqual(ui.mode, 'review')
                self.assertEqual(load_spectrum(path).calibration['scale'], expected)
                ui._scale_active = False
                ui._display_capture()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                ui._select_peak(ui._peaks.positions[0])
                self.assertEqual(ui._review_peak_label,
                                 '550.0 nm' if operation == 'modify' else 'Pixel 350')
                # A later instrument recalibration must not affect this saved capture.
                ui.calibration_settings['scale'] = {200: 1000.0, 500: 900.0}
                self.assertEqual(load_spectrum(path).calibration['scale'], expected)

    def test_back_discards_add_modify_delete_and_reset_then_loads_fresh_draft(self):
        for operation in ('add', 'modify', 'delete', 'reset'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                path = capture.save_capture(self.make_record(), directory)
                original_bytes = path.read_bytes()
                store = SettingsStore(Path(directory) / '.spectrometer_config')
                store.update(calibration={'scale': {200: 700.0, 500: 450.0}})
                original_settings = deepcopy(store.data['calibration'])
                camera = Mock()
                camera.settings_snapshot.return_value = deepcopy(DEFAULTS['camera'])
                persist = Mock(side_effect=lambda data: store.update(calibration=data))
                ui = self.make_ui(directory, path, camera=camera,
                                  calibration_settings=deepcopy(original_settings),
                                  on_calibration_changed=persist)
                camera.set_calibration.reset_mock()
                self.assertEqual(ui._scale_labels, original_settings['scale'])
                if operation == 'add':
                    ui._update_scale(350, 550.0)
                elif operation == 'modify':
                    ui._update_scale(500, 400.0)
                elif operation == 'delete':
                    ui._update_scale(200, None)
                else:
                    ui._reset_scale()
                    ui._confirm_scale_reset()
                self.assertNotEqual(ui._scale_labels, original_settings['scale'])
                ui._select_peak(ui._peaks.positions[0])
                ui._back_peak()
                ui._cancel_scale()
                self.assertEqual(ui.mode, 'review')
                self.assertIsNone(ui._peaks.marker)
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(ui.calibration_settings, original_settings)
                self.assertEqual(SettingsStore(store.path).data['calibration'], original_settings)
                persist.assert_not_called()
                camera.set_calibration.assert_not_called()
                ui._display_capture()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                self.assertEqual(ui.mode, 'saved')
                self.assertEqual(ui._scale_labels, original_settings['scale'])
                self.assertIsNot(ui._scale_labels, ui.calibration_settings['scale'])

    def test_accept_persists_settings_and_capture_then_publishes_camera_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_record()
            original['peak_labels'] = {350: 'Reference line'}
            path = capture.save_capture(original, directory)
            store = SettingsStore(Path(directory) / '.spectrometer_config')
            before = deepcopy(store.data['calibration'])
            camera = Mock()
            camera.settings_snapshot.return_value = deepcopy(DEFAULTS['camera'])
            ui = self.make_ui(directory, path, camera=camera,
                              calibration_settings=deepcopy(before),
                              on_calibration_changed=lambda data: store.update(calibration=data))
            camera.set_calibration.reset_mock()
            ui._update_scale(200, 700.0)
            ui._update_scale(500, 400.0)
            self.assertEqual(ui.calibration_settings, before)
            self.assertEqual(store.data['calibration'], before)
            camera.set_calibration.assert_not_called()
            ui._finish_scale()
            ui._scale_save_future.result(timeout=5)
            ui._poll_review()
            expected = dict(before, scale={200: 700.0, 500: 400.0})
            self.assertEqual(ui.calibration_settings, expected)
            self.assertEqual(SettingsStore(store.path).data['calibration'], expected)
            camera.set_calibration.assert_called_once_with(expected)
            updated = pickle.loads(path.read_bytes())
            self.assertEqual(updated['instrument_settings']['calibration_settings']['scale'], expected['scale'])
            self.assertEqual(updated['peak_labels'], original['peak_labels'])
            for key in ('name', 'timestamp', 'spectrum_roi'):
                self.assertEqual(updated[key], original[key])
            for key in ('spectrum_intensity', 'averaged_camera_output'):
                np.testing.assert_array_equal(updated[key], original[key])

    def test_settings_write_failure_does_not_publish_draft_or_rewrite_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = capture.save_capture(self.make_record(), directory)
            original_bytes = path.read_bytes()
            store = SettingsStore(Path(directory) / '.spectrometer_config')
            before = deepcopy(store.data['calibration'])
            camera = Mock()
            camera.settings_snapshot.return_value = deepcopy(DEFAULTS['camera'])
            ui = self.make_ui(directory, path, camera=camera,
                              calibration_settings=deepcopy(before),
                              on_calibration_changed=lambda data: store.update(calibration=data))
            camera.set_calibration.reset_mock()
            ui._update_scale(500, 400.0)
            with patch.object(store, '_write', side_effect=OSError('Disk full')):
                ui._finish_scale()
                self.assertIsInstance(ui._scale_save_future.exception(timeout=5), OSError)
                with self.assertLogs('spectrometer.ui', level='ERROR'):
                    ui._poll_review()
            self.assertEqual(ui.mode, 'saved')
            self.assertEqual([label for label, _, _ in ui.buttons], ['Retry', 'Cancel'])
            self.assertEqual(ui._scale_labels, {500: 400.0})
            self.assertEqual(ui.calibration_settings, before)
            self.assertEqual(SettingsStore(store.path).data['calibration'], before)
            self.assertEqual(path.read_bytes(), original_bytes)
            camera.set_calibration.assert_not_called()


if __name__ == '__main__':
    unittest.main()
