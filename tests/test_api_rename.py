from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from spectrometer.server import running_server


class RenameTests(unittest.TestCase):
    def request(self, server, method, route, data=None, headers=None):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request(method, route, data, headers or {})
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()

    def rename(self, server, data, spectrum_id='1234'):
        return self.request(server, 'PATCH', '/spectrum/' + spectrum_id,
                            json.dumps(data), {'Content-Type': 'application/json'})

    def test_rename_preserves_data_identity_and_refreshes_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-test.pkl'
            record = {
                'timestamp': datetime(1970, 1, 1, 0, 0, 1, 234000, tzinfo=timezone.utc),
                'name': 'Original',
                'averaged_camera_output': np.arange(12, dtype=np.uint8).reshape(2, 2, 3),
                'spectrum_intensity': np.array([10, 20]),
                'instrument_settings': {'calibration_settings': {'scale': {1: 500, 2: 600}}},
            }
            path.write_bytes(pickle.dumps(record))
            with running_server('127.0.0.1', 0, capture_directory=directory) as server:
                self.assertEqual(self.rename(server, {'name': '  New lamp λ  '}),
                                 (200, {'id': 1234, 'name': 'New lamp λ'}))
                status, entries = self.request(server, 'GET', '/list')
                self.assertEqual(status, 200)
                self.assertEqual(entries[0]['name'], 'New lamp λ')
                self.assertEqual(entries[0]['id'], 1234)
                status, downloaded = self.request(server, 'GET', '/spectrum/1234')
                self.assertEqual(status, 200)
                self.assertEqual(downloaded['name'], 'New lamp λ')
            saved = pickle.loads(path.read_bytes())
            self.assertEqual(saved['timestamp'], record['timestamp'])
            self.assertEqual(saved['instrument_settings'], record['instrument_settings'])
            for key in ('averaged_camera_output', 'spectrum_intensity'):
                np.testing.assert_array_equal(saved[key], record[key])
            self.assertEqual(list(Path(directory).glob('spectrum-*.pkl')), [path])

    def test_validation_missing_ambiguous_busy_and_failed_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-test.pkl'
            record = {'timestamp': datetime(1970, 1, 1, 0, 0, 1, 234000, tzinfo=timezone.utc),
                      'name': 'Original'}
            original = pickle.dumps(record)
            path.write_bytes(original)
            with running_server('127.0.0.1', 0, capture_directory=directory) as server:
                for payload in ({}, [], None, {'name': 1}, {'name': ''}, {'name': '  '},
                                {'name': 'a' * 65}, {'name': 'Lamp', 'extra': True}):
                    self.assertEqual(self.rename(server, payload)[0], 400, payload)
                self.assertEqual(self.request(server, 'PATCH', '/spectrum/1234', '{',
                                 {'Content-Type': 'application/json'})[0], 400)
                self.assertEqual(self.request(server, 'PATCH', '/spectrum/1234', '{}')[0], 415)
                self.assertEqual(self.rename(server, {'name': 'Lamp'}, 'missing')[0], 404)
                self.assertEqual(self.rename(server, {'name': 'Lamp'}, '1235')[0], 404)
                self.assertEqual(self.rename(server, {'name': 'Lamp'}, '../1234')[0], 404)
                with server.raw_slots:
                    self.assertEqual(self.rename(server, {'name': 'Busy'})[0], 503)
                    self.assertEqual(self.request(server, 'DELETE', '/spectrum/1234')[0], 503)
                with patch('spectrometer.capture.os.replace', side_effect=OSError('Read only')):
                    with self.assertLogs('spectrometer.server', level='ERROR'):
                        self.assertEqual(self.rename(server, {'name': 'Failure'})[0], 500)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob('.spectrum-*')), [])
                duplicate = Path(directory) / 'spectrum-duplicate.pkl'
                duplicate.write_bytes(original)
                server.catalog.upsert_record(duplicate, record)
                self.assertEqual(self.rename(server, {'name': 'Ambiguous'})[0], 409)
