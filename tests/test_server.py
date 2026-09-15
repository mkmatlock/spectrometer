from http.client import HTTPConnection
import json
import unittest

from spectrometer.server import running_server


class APITests(unittest.TestCase):
    def test_empty_routes_and_invalid_download(self):
        with running_server('127.0.0.1', 0) as server:
            for method, path, expected in [('GET', '/list', []),
                                           ('GET', '/download/123', {}),
                                           ('GET', '/delete/123', {}),
                                           ('GET', '/settings', {})]:
                with self.subTest(method=method, path=path):
                    connection = HTTPConnection(*server.server_address, timeout=2)
                    try:
                        connection.request(method, path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader('Content-Type'), 'application/json')
                        self.assertEqual(json.loads(response.read()), expected)
                    finally:
                        connection.close()
            for path in ('/download/not-a-number', '/download/1/extra', '/unknown'):
                connection = HTTPConnection(*server.server_address, timeout=2)
                try:
                    connection.request('GET', path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read()
                finally:
                    connection.close()
        self.assertEqual(server.socket.fileno(), -1)
