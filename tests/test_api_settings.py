from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spectrometer.config import SettingsStore
from spectrometer.server import running_server


class APISettingsTests(unittest.TestCase):
    def get_settings(self, server):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request('GET', '/settings')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read())
        finally:
            connection.close()

    def test_current_configuration_and_updates_without_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / '.spectrometer_config')
            with running_server('127.0.0.1', 0, capture_directory=directory, settings_store=store) as server:
                result = self.get_settings(server)
                self.assertEqual(result['camera'], {'frame_rate': 5, 'resolution': [4056, 3040],
                                                    'exposure_us': None, 'frame_averaging': 3})
                self.assertEqual(result['calibration'], {
                    'scale': {}, 'sensor_area': [0, 1550, 3500, 1800], 'channel_ranges': {}})
                store.update(camera={'frame_rate': 4, 'exposure_us': 20000},
                             calibration={'scale': {100: 650, 200: 500}, 'sensor_area': (100, 1500, 3500, 1800)})
                result = self.get_settings(server)
                self.assertEqual(result['camera']['exposure_us'], 20000)
                self.assertEqual(result['camera']['frame_rate'], 4)
                self.assertEqual(result['calibration']['scale'], {'100': 650, '200': 500})
                self.assertEqual(result['calibration']['sensor_area'], [100, 1500, 3500, 1800])
                result['calibration']['scale'].clear()
                self.assertEqual(store.data['calibration']['scale'], {100: 650, 200: 500})

    def test_standalone_reads_persistent_configuration(self):
        with tempfile.TemporaryDirectory() as directory, patch('pathlib.Path.home', return_value=Path(directory)):
            store = SettingsStore()
            store.update(camera={'frame_rate': 3})
            with running_server('127.0.0.1', 0, capture_directory=directory) as server:
                self.assertEqual(self.get_settings(server)['camera']['frame_rate'], 3)
                store.update(camera={'frame_rate': 2})
                self.assertEqual(self.get_settings(server)['camera']['frame_rate'], 2)
