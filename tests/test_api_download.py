import base64
from concurrent.futures import Future
from datetime import datetime, timezone, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import tempfile
import threading
import unittest
from unittest.mock import Mock

import numpy as np

from spectrometer.server import APIHandler, running_server


class RecordingWriter:
    """Capture socket writes, optionally failing after the first body chunk."""

    def __init__(self, transfer_error=None):
        self.chunks = []
        self.transfer_error = transfer_error
        self.failed = False

    def write(self, data):
        data = bytes(data)
        if self.transfer_error is not None and not self.failed:
            # A monolithic write can make partial progress before timing out;
            # bounded writes finish one chunk before the next write fails.
            if self.chunks or len(data) > 65536:
                if not self.chunks:
                    self.chunks.append(data[:65536])
                self.failed = True
                raise self.transfer_error('Simulated stalled/disconnected client')
        self.chunks.append(data)
        return len(data)


class DownloadTests(unittest.TestCase):
    def download_handler(self, record, writer=None):
        handler = object.__new__(APIHandler)
        handler.path = '/spectrum/1789512585218'
        handler.server = Mock(raw_slots=threading.BoundedSemaphore(1))
        handler._find_spectrum = Mock(return_value=(Path('spectrum-test.pkl'), record))
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = writer if writer is not None else RecordingWriter()
        handler.close_connection = False
        return handler

    def assert_transfer_slot_released(self, handler):
        self.assertTrue(handler.server.raw_slots.acquire(blocking=False))
        self.assertFalse(handler.server.raw_slots.acquire(blocking=False))
        handler.server.raw_slots.release()

    def test_large_download_is_complete_in_bounded_writes(self):
        raw = np.arange(512 * 2048 * 3, dtype=np.uint8).reshape(512, 2048, 3)
        timestamp = datetime(2026, 9, 15, 12, 34, 56, tzinfo=timezone.utc)
        handler = self.download_handler({
            'timestamp': timestamp, 'name': 'Large spectrum',
            'averaged_camera_output': raw, 'spectrum_bar': b'\x00\xff',
            'spectrum_intensity': np.array([123, 456], np.int32),
            'instrument_settings': {'calibration_settings': {'scale': {1: 500}}},
        })

        handler.spectrum('1789512585218')

        handler.send_response.assert_called_once_with(200)
        handler.end_headers.assert_called_once_with()
        self.assertGreater(len(handler.wfile.chunks), 1)
        self.assertLessEqual(max(map(len, handler.wfile.chunks)), 65536)
        body = b''.join(handler.wfile.chunks)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(int(headers['Content-Length']), len(body))
        downloaded = json.loads(body)
        self.assertEqual(downloaded['filename'], 'spectrum-test.pkl')
        self.assertEqual(downloaded['timestamp'], timestamp.isoformat())
        self.assertEqual(downloaded['averaged_camera_output']['dtype'], 'uint8')
        self.assertEqual(downloaded['averaged_camera_output']['shape'], [512, 2048, 3])
        self.assertEqual(base64.b64decode(downloaded['averaged_camera_output']['data']), raw.tobytes())
        self.assertEqual(base64.b64decode(downloaded['spectrum_bar']['data']), b'\x00\xff')
        self.assertEqual(downloaded['spectrum_intensity'], [123, 456])
        self.assertEqual(downloaded['instrument_settings']['calibration_settings']['scale'], {'1': 500})
        self.assert_transfer_slot_released(handler)

    def test_interrupted_download_never_appends_an_error_response(self):
        record = {'averaged_camera_output': np.zeros((256, 1024, 3), np.uint8)}
        for transfer_error in (TimeoutError, BrokenPipeError, ConnectionResetError):
            with self.subTest(transfer_error=transfer_error.__name__):
                writer = RecordingWriter(transfer_error)
                handler = self.download_handler(record, writer)

                handler.spectrum('1789512585218')

                self.assertTrue(writer.failed)
                handler.send_response.assert_called_once_with(200)
                handler.end_headers.assert_called_once_with()
                self.assertEqual(len(writer.chunks), 1)
                self.assertTrue(handler.close_connection)
                self.assert_transfer_slot_released(handler)

    def test_serialization_failure_sends_a_single_complete_error(self):
        handler = self.download_handler({'averaged_camera_output': np.zeros((2, 2, 3), np.uint8), 'invalid_value': object()})
        with self.assertLogs('spectrometer.server', level='ERROR'):
            handler.spectrum('1789512585218')

        handler.send_response.assert_called_once_with(500)
        handler.end_headers.assert_called_once_with()
        body = b''.join(handler.wfile.chunks)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertEqual(json.loads(body), {'error': 'Cannot read spectrum data'})
        self.assert_transfer_slot_released(handler)

    def test_download_matches_capture_and_list_id_without_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-1970-01-01-02-00-01.pkl'
            record = {'timestamp': datetime(1970, 1, 1, 2, 0, 1, 234567,
                                           tzinfo=timezone(timedelta(hours=2))),
                      'name': 'Lamp', 'averaged_camera_output': np.array([[[1, 2, 255]]], np.uint8),
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
                self.assertEqual(base64.b64decode(downloaded['averaged_camera_output']['data']), bytes([1, 2, 255]))
                self.assertEqual(downloaded['spectrum_intensity'], [123, 456])
                self.assertEqual(downloaded['instrument_settings']['calibration_settings']['scale'], {'1': 500})
                for route in ('/spectrum/1000', '/spectrum/1235', '/spectrum/../../etc/passwd'):
                    self.assertEqual(get(route)[0], 404)
            self.assertEqual(path.read_bytes(), original)
