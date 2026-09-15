from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import unittest

import numpy as np

from spectrometer.server import running_server

class APIListTests(unittest.TestCase):
    def test_saved_metadata_order_epoch_ids_and_legacy_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            records = [
                {'timestamp': datetime(1970, 1, 1, 2, 0, 1, 234567, tzinfo=timezone(timedelta(hours=2))),
                 'name': 'Lamp'},
                {'timestamp': datetime(1970, 1, 1, tzinfo=timezone.utc)},
                {'timestamp': datetime(1970, 1, 2, tzinfo=timezone.utc),
                 'name': 'No calibration'},
            ]
            for i, record in enumerate(records):
                record['raw_camera_output'] = bytes(10)
                (Path(directory) / f'spectrum-{i}.pkl').write_bytes(pickle.dumps(record))
            (Path(directory) / 'spectrum-broken.pkl').write_bytes(b'broken')
            (Path(directory) / 'unrelated.pkl').write_bytes(pickle.dumps(records[0]))
            with running_server('127.0.0.1', 0, capture_directory=directory) as server:
                connection = HTTPConnection(*server.server_address, timeout=5)
                try:
                    with self.assertLogs('spectrometer.server', level='ERROR'):
                        connection.request('GET', '/list')
                        response = connection.getresponse()
                        entries = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                finally:
                    connection.close()
            self.assertEqual([entry['id'] for entry in entries], [86400000, 1234, 0])
            self.assertEqual(entries[1], {'id': 1234, 'name': 'Lamp',
                             'timestamp': '1970-01-01 02:00:01'})
            self.assertEqual(entries[2]['name'], 'Unnamed spectrum')
