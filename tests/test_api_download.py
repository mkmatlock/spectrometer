import base64
from concurrent.futures import Future
from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np

from spectrometer.server import running_server


class DownloadTests(unittest.TestCase):
    def test_download_matches_capture_and_list_id_without_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-1970-01-01-02-00-01.pkl'
            record = {'timestamp': datetime(1970, 1, 1, 2, 0, 1, 234567,
                                           tzinfo=timezone(timedelta(hours=2))),
                      'name': 'Lamp', 'raw_camera_output': np.array([[1, 2, 255]], np.uint8),
                      'spectrum_intensity': np.array([123, 456], np.int32),
                      'spectrum_bar': b'abc', 'spectrum_roi': (0, 1550, 3500, 1800),
                      'instrument_settings': {'calibration_settings': {'scale': {1: 500}}}}
            original = pickle.dumps(record)
            path.write_bytes(original)
            future = Future()
            future.set_result((path, record))
            camera = Mock()
            camera.request_api_capture.return_value = future
            with running_server('127.0.0.1', 0, camera, directory) as server:
                def get(route):
                    connection = HTTPConnection(*server.server_address, timeout=5)
                    try:
                        connection.request('GET', route)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()
                status, captured = get('/capture?name=Lamp')
                self.assertEqual(status, 200)
                server.camera = None
                _, entries = get('/list')
                self.assertEqual(entries[0]['id'], 1234)
                self.assertEqual(captured, entries[0]['id'])
                status, downloaded = get(f'/spectrum/{captured}')
                self.assertEqual(status, 200)
                self.assertEqual(downloaded['filename'], path.name)
                self.assertEqual(downloaded['name'], 'Lamp')
                self.assertEqual(base64.b64decode(downloaded['raw_camera_output']['data']), bytes([1, 2, 255]))
                self.assertEqual(downloaded['spectrum_intensity'], [123, 456])
                self.assertEqual(downloaded['instrument_settings']['calibration_settings']['scale'], {'1': 500})
                for route in ('/spectrum/1000', '/spectrum/1235', '/spectrum/../../etc/passwd'):
                    self.assertEqual(get(route)[0], 404)
            self.assertEqual(path.read_bytes(), original)
