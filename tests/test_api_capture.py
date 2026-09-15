from concurrent.futures import Future
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from spectrometer.camera import CameraStream, SpectrumFrame
from spectrometer.server import running_server


class APICaptureTests(unittest.TestCase):
    def get(self, server, path):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_capture_saves_and_returns_only_integer_id(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = CameraStream(capture_directory=directory)
            camera._started = True
            camera._raw_config = {'size': (4056, 3040)}
            camera.set_calibration({'scale': {100: 700, 200: 500}})
            ready = threading.Event()
            original = camera.request_api_capture
            def request_capture(name):
                future = original(name)
                ready.set()
                return future
            def frame_callback():
                if ready.wait(5):
                    request = Mock()
                    request.get_metadata.return_value = {'ExposureTime': 1234}
                    request.make_array.return_value = np.array([[1, 2, 3]], np.uint8)
                    camera._queue_capture(request, SpectrumFrame(b'abc', np.array([10, 20], np.int32)),
                                          datetime(2026, 9, 14, tzinfo=timezone.utc))
            worker = threading.Thread(target=frame_callback)
            worker.start()
            try:
                with patch.object(camera, 'request_api_capture', side_effect=request_capture), \
                        running_server('127.0.0.1', 0, camera) as server:
                    status, data = self.get(server, '/capture?name=Test%20lamp')
                self.assertEqual(status, 200)
                self.assertIs(type(data), int)
                self.assertEqual(data, 1789344000000)
                record = pickle.loads(next(Path(directory).glob('spectrum-*.pkl')).read_bytes())
                self.assertEqual(record['name'], 'Test lamp')
                np.testing.assert_array_equal(record['spectrum_intensity'], [10, 20])
                np.testing.assert_array_equal(record['raw_camera_output'], [[1, 2, 3]])
                self.assertEqual(record['instrument_settings']['calibration_settings']['scale'], {100: 700, 200: 500})
            finally:
                worker.join()
                camera.close()

    def test_validation_unavailable_busy_and_write_failure(self):
        with running_server('127.0.0.1', 0) as server:
            for query in ('', '?name=', '?name=a&name=b', '?name=a&other=b', '?name=' + 'a'*65):
                self.assertEqual(self.get(server, '/capture' + query)[0], 400)
            self.assertEqual(self.get(server, '/capture?name=Lamp')[0], 503)
            camera = server.camera = Mock()
            camera.request_api_capture.return_value = None
            self.assertEqual(self.get(server, '/capture?name=Lamp')[0], 409)
            future = Future()
            future.set_exception(OSError('Disk full'))
            camera.request_api_capture.return_value = future
            with self.assertLogs('spectrometer.server', level='ERROR'):
                self.assertEqual(self.get(server, '/capture?name=Lamp')[0], 500)

    def test_pause_cancels_pending_api_request(self):
        camera = CameraStream()
        camera._camera = Mock()
        camera._started = True
        try:
            future = camera.request_api_capture('Test')
            self.assertIsNone(camera.request_api_capture('Concurrent'))
            self.assertFalse(camera.request_capture())
            camera.pause()
            with self.assertRaises(RuntimeError):
                future.result(timeout=1)
            self.assertFalse(camera._capture_busy)
        finally:
            camera.close()
