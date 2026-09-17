from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from spectrometer.camera import CameraStream, SpectrumFrame
from spectrometer.capture import save_capture, update_peak_label
from spectrometer.review import ReviewList, load_channels, load_spectrum
from tests.spectrum_fixtures import spectrum_record


class ReviewLabelStorageTests(unittest.TestCase):
    def make_record(self, **overrides):
        record = spectrum_record(spectrum_roi=(100, 200, 600, 204))
        record['instrument_settings']['calibration_settings']['scale'] = {200: 650, 500: 450}
        record['averaged_camera_output'][:] = (10, 20, 30)
        record.update(overrides)
        return record

    def assert_record_equal(self, actual, expected):
        self.assertEqual(set(actual), set(expected))
        for key, value in expected.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(actual[key], value)
                self.assertEqual(actual[key].dtype, value.dtype)
            else:
                self.assertEqual(actual[key], value)

    def test_add_delete_and_reload_preserve_other_data(self):
        with tempfile.TemporaryDirectory() as directory:
            original = self.make_record(peak_labels={200: 'Sodium'})
            path = save_capture(original, directory)
            result = update_peak_label(path, 350, '  Hydrogen alpha  ')
            self.assertEqual(result, {200: 'Sodium', 350: 'Hydrogen alpha'})
            expected = deepcopy(original)
            expected['peak_labels'][350] = 'Hydrogen alpha'
            self.assert_record_equal(pickle.loads(path.read_bytes()), expected)
            self.assertEqual(load_spectrum(path).peak_labels, expected['peak_labels'])
            result.clear()
            self.assertEqual(load_spectrum(path).peak_labels, expected['peak_labels'])
            self.assertEqual(update_peak_label(path, 200, None), {350: 'Hydrogen alpha'})
            del expected['peak_labels'][200]
            self.assert_record_equal(pickle.loads(path.read_bytes()), expected)

    def test_optional_annotations_do_not_change_unlabeled_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(self.make_record(), directory)
            original = path.read_bytes()
            self.assertEqual(load_spectrum(path).peak_labels, {})
            self.assertTrue(all(not frame.peak_labels for frame in load_channels(path).values()))
            self.assertEqual(path.read_bytes(), original)

    def test_labels_survive_every_channel_combination(self):
        with tempfile.TemporaryDirectory() as directory:
            labels = {200: 'Sodium', 400: 'Absorption valley'}
            path = save_capture(self.make_record(peak_labels=labels), directory)
            review = ReviewList(directory)
            try:
                original = load_spectrum(path)
                channels = load_channels(path)
                for frame in channels.values():
                    self.assertEqual(frame.peak_labels, labels)
                review._channel_cache = {'All': original, **channels}
                for selected in ((), ('Red',), ('Green', 'Blue'), ('Red', 'Green', 'Blue')):
                    with self.subTest(selected=selected):
                        self.assertEqual(review._channel_frame(selected).peak_labels, labels)
                original.peak_labels[300] = 'New annotation'
                self.assertEqual(review._channel_frame(('Red',)).peak_labels,
                                 {**labels, 300: 'New annotation'})
            finally:
                review.close()

    def test_invalid_edits_leave_capture_unchanged(self):
        invalid = [(99, 'Outside'), (600, 'Outside'), (True, 'Boolean'),
                   (200.0, 'Float'), (200, ''), (200, '  '), (200, 'x' * 65),
                   (200, 42)]
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(self.make_record(peak_labels={300: 'Retained'}), directory)
            original = path.read_bytes()
            for pixel, label in invalid:
                with self.subTest(pixel=pixel, label=label), self.assertRaises(ValueError):
                    update_peak_label(path, pixel, label)
                self.assertEqual(path.read_bytes(), original)

    def test_invalid_saved_annotations_are_rejected(self):
        invalid = [None, [], {'200': 'Text'}, {True: 'Text'}, {99: 'Outside'},
                   {600: 'Outside'}, {200: ''}, {200: '  '}, {200: 42}, {200: 'x' * 65}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-test.pkl'
            for labels in invalid:
                path.write_bytes(pickle.dumps(self.make_record(peak_labels=labels)))
                for loader in (load_spectrum, load_channels):
                    with self.subTest(labels=labels, loader=loader), self.assertRaises(ValueError):
                        loader(path)

    def test_failed_annotation_write_preserves_original(self):
        for operation in ('pickle.dump', 'os.replace'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                path = save_capture(self.make_record(peak_labels={200: 'Retained'}), directory)
                original = path.read_bytes()
                with patch('spectrometer.capture.' + operation, side_effect=OSError('Disk full')):
                    with self.assertRaises(OSError):
                        update_peak_label(path, 300, 'Not saved')
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob('.spectrum-*')), [])

    def test_concurrent_annotations_preserve_both_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(self.make_record(), directory)
            barrier = threading.Barrier(2)

            def annotate(pixel):
                barrier.wait(timeout=5)
                return update_peak_label(path, pixel, str(pixel))

            with ThreadPoolExecutor(max_workers=2) as workers:
                futures = [workers.submit(annotate, pixel) for pixel in (200, 300)]
                for future in futures:
                    future.result(timeout=5)
            self.assertEqual(load_spectrum(path).peak_labels, {200: '200', 300: '300'})

    def test_review_saves_the_loaded_path_snapshotted_at_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            review = ReviewList(directory)
            entered, release = threading.Event(), threading.Event()
            first = Path(directory) / 'spectrum-selected.pkl'
            second = Path(directory) / 'spectrum-other.pkl'
            review.loaded_path = first

            def block():
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError('Test did not release save worker')

            try:
                review._names_executor.submit(block)
                self.assertTrue(entered.wait(timeout=5))
                with patch('spectrometer.capture.update_peak_label', return_value={200: 'Text'}) as save:
                    future = review.save_peak_label(200, 'Text')
                    review.loaded_path = second
                    release.set()
                    self.assertEqual(future.result(timeout=5), {200: 'Text'})
                    save.assert_called_once_with(first, 200, 'Text')
                review.loaded_path = None
                with self.assertRaises(ValueError):
                    review.save_peak_label(200, 'Text')
            finally:
                release.set()
                review.close()

    def test_new_named_capture_starts_with_empty_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            record = self.make_record()
            camera = CameraStream(capture_directory=directory)
            camera._raw_config = record['instrument_settings']['raw_camera_format']
            request = Mock()
            request.get_metadata.return_value = {'ExposureTime': 1000}
            frame = SpectrumFrame(record['spectrum_bar'], record['spectrum_intensity'],
                                  record['spectrum_roi'], maximum=record['spectrum_maximum'])
            try:
                camera._queue_capture(request, frame, record['timestamp'],
                                      record['averaged_camera_output'])
                draft = camera.take_capture_draft()
                self.assertEqual(draft['peak_labels'], {})
                path = camera.save_named_capture(draft, 'New capture').result(timeout=5)
                self.assertEqual(pickle.loads(path.read_bytes())['peak_labels'], {})
            finally:
                camera.close()


if __name__ == '__main__':
    unittest.main()
