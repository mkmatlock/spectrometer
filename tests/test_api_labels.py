from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectrometer.capture import save_capture
from spectrometer.catalog import timestamp_id
from spectrometer.server import running_server
from tests.spectrum_fixtures import spectrum_record


class LabelAPITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original = spectrum_record(spectrum_roi=(100, 200, 600, 204),
                                        peak_labels={200: 'Existing feature'})
        self.path = save_capture(self.original, self.directory.name)
        self.spectrum_id = timestamp_id(self.original['timestamp'])
        self.route = '/spectrum/' + str(self.spectrum_id)
        self.server = self.enterContext(running_server(
            '127.0.0.1', 0, capture_directory=self.directory.name))

    def request(self, method, route=None, payload=None, raw=None, headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            if raw is None and payload is not None:
                raw = json.dumps(payload)
                headers = {'Content-Type': 'application/json'}
            connection.request(method, route or self.route, raw, headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()

    def label(self, pixel, label, route=None):
        return self.request('PATCH', route, {'peak_label': {'pixel': pixel, 'label': label}})

    def assert_unchanged_data(self, saved, labels):
        expected = deepcopy(self.original)
        expected['peak_labels'] = labels
        self.assertEqual(set(saved), set(expected))
        for key, value in expected.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(saved[key], value)
                self.assertEqual(saved[key].dtype, value.dtype)
            else:
                self.assertEqual(saved[key], value, key)

    def test_add_update_delete_and_get_preserve_measurements_and_calibration(self):
        self.assertEqual(self.label(350, '  Hα absorption  '),
                         (200, {'id': self.spectrum_id, 'peak_labels':
                                {'200': 'Existing feature', '350': 'Hα absorption'}}))
        status, downloaded = self.request('GET')
        self.assertEqual(status, 200)
        self.assertEqual(downloaded['peak_labels'],
                         {'200': 'Existing feature', '350': 'Hα absorption'})
        self.assertEqual(self.label(350, 'Changed')[0], 200)
        saved = pickle.loads(self.path.read_bytes())
        self.assert_unchanged_data(saved, {200: 'Existing feature', 350: 'Changed'})
        self.assertEqual(self.label(200, None),
                         (200, {'id': self.spectrum_id, 'peak_labels': {'350': 'Changed'}}))
        self.assert_unchanged_data(pickle.loads(self.path.read_bytes()), {350: 'Changed'})
        self.assertEqual(self.request('GET')[1]['peak_labels'], {'350': 'Changed'})
        # Retrying a deletion is harmless and preserves unrelated annotations.
        self.assertEqual(self.label(200, None)[1]['peak_labels'], {'350': 'Changed'})

    def test_first_annotation_and_boundary_pixels(self):
        record = deepcopy(self.original)
        del record['peak_labels']
        self.path.write_bytes(pickle.dumps(record))
        self.assertEqual(self.label(100, 'Left edge')[1]['peak_labels'], {'100': 'Left edge'})
        self.assertEqual(self.label(599, 'Right edge')[1]['peak_labels'],
                         {'100': 'Left edge', '599': 'Right edge'})

    def test_invalid_payloads_and_roi_leave_file_unchanged(self):
        original = self.path.read_bytes()
        invalid = [
            {}, [], {'peak_label': None}, {'peak_label': []}, {'peak_label': 'Text'},
            {'peak_label': {'pixel': 200}}, {'peak_label': {'label': 'Text'}},
            {'peak_label': {'pixel': 200, 'label': 'Text', 'extra': True}},
            {'peak_label': {'pixel': 200, 'label': 'Text'}, 'extra': True},
            {'peak_label': {'pixel': 200, 'label': 'Text'}, 'name': 'Rename'},
        ]
        invalid += [{'peak_label': {'pixel': pixel, 'label': label}}
                    for pixel, label in [(True, 'Text'), (200.0, 'Text'), ('200', 'Text'),
                                         (None, 'Text'), (200, ''), (200, '  '),
                                         (200, 'a' * 65), (200, 123), (200, []),
                                         (99, 'Outside'), (600, 'Outside'), (99, None)]]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.request('PATCH', payload=payload)[0], 400)
                self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.request('PATCH', raw='{',
                         headers={'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('PATCH', raw='{}')[0], 415)
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_busy_ambiguous_and_disappearing_spectra(self):
        self.assertEqual(self.label(200, 'Missing', '/spectrum/1')[0], 404)
        with self.server.raw_slots:
            self.assertEqual(self.label(200, 'Busy')[0], 503)
        with patch('spectrometer.server.update_peak_label', side_effect=FileNotFoundError):
            self.assertEqual(self.label(200, 'Missing')[0], 404)
        duplicate = Path(self.directory.name) / 'spectrum-duplicate.pkl'
        duplicate.write_bytes(self.path.read_bytes())
        self.server.catalog.upsert_record(duplicate, self.original)
        self.server.catalog.upsert_record(self.path, self.original)
        self.assertEqual(self.label(200, 'Ambiguous')[0], 409)

    def test_failed_write_and_index_lookup_leave_file_unchanged(self):
        original = self.path.read_bytes()
        with patch('spectrometer.capture.os.replace', side_effect=OSError('Disk full')):
            with self.assertLogs('spectrometer.server', level='ERROR'):
                self.assertEqual(self.label(200, 'Not saved')[0], 500)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(Path(self.directory.name).glob('.spectrum-*')), [])
        with patch.object(self.server.catalog, 'find', side_effect=OSError('Cannot read index')):
            with self.assertLogs('spectrometer.server', level='ERROR'):
                self.assertEqual(self.label(200, 'Not saved')[0], 500)
        self.assertEqual(self.path.read_bytes(), original)
        # Failed requests release the operation semaphore so a retry succeeds.
        self.assertEqual(self.label(200, 'Retry')[0], 200)

    def test_rename_and_annotation_preserve_each_other(self):
        self.assertEqual(self.label(350, 'New feature')[0], 200)
        self.assertEqual(self.request('PATCH', payload={'name': ' Renamed '}),
                         (200, {'id': self.spectrum_id, 'name': 'Renamed'}))
        self.assertEqual(self.label(200, None)[0], 200)
        downloaded = self.request('GET')[1]
        self.assertEqual(downloaded['name'], 'Renamed')
        self.assertEqual(downloaded['peak_labels'], {'350': 'New feature'})
        self.assertEqual(self.request('GET', '/list')[1][0]['name'], 'Renamed')

    def test_annotations_asset_is_served(self):
        assets = Path(self.directory.name) / 'web'
        assets.mkdir()
        (assets / 'annotations.js').write_text('export const test = true;')
        with patch('spectrometer.server.WEB_DIRECTORY', assets):
            connection = HTTPConnection(*self.server.server_address, timeout=5)
            try:
                connection.request('GET', '/annotations.js?v=1')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'text/javascript; charset=utf-8')
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(response.read(), b'export const test = true;')
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
