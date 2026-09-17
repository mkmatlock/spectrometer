from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

from spectrometer.server import running_server
from tests.spectrum_fixtures import spectrum_record


class DeleteTests(unittest.TestCase):
    def test_delete_only_matching_id_and_removed_routes_do_not_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'spectrum-{i}.pkl' for i in (1, 2)]
            for i, path in enumerate(paths, 1):
                path.write_bytes(pickle.dumps(spectrum_record(timestamp=datetime(1970, 1, 1, 0, 0, i, tzinfo=timezone.utc))))
            with running_server('127.0.0.1', 0, capture_directory=directory) as server:
                def request(method, route):
                    connection = HTTPConnection(*server.server_address, timeout=5)
                    try:
                        connection.request(method, route)
                        response = connection.getresponse()
                        return response.status, response.read()
                    finally:
                        connection.close()
                for method, route in [('GET', '/delete/1000'), ('DELETE', '/delete/1000'),
                                      ('GET', '/download/1000'), ('DELETE', '/spectrum/../1000')]:
                    self.assertEqual(request(method, route)[0], 404)
                self.assertTrue(paths[0].exists())
                with patch.object(Path, 'unlink', side_effect=PermissionError('Read only')):
                    with self.assertLogs('spectrometer.server', level='ERROR'):
                        self.assertEqual(request('DELETE', '/spectrum/1000')[0], 500)
                self.assertTrue(paths[0].exists())
                self.assertEqual(request('DELETE', '/spectrum/1000'), (204, b''))
                self.assertFalse(paths[0].exists())
                self.assertTrue(paths[1].exists())
                self.assertEqual(request('DELETE', '/spectrum/1000')[0], 404)
                self.assertEqual(request('GET', '/spectrum/1000')[0], 404)
