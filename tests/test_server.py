from http.client import HTTPConnection
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from spectrometer.server import running_server


class APITests(unittest.TestCase):
    def test_empty_routes_and_invalid_download(self):
        with tempfile.TemporaryDirectory() as directory, \
                running_server('127.0.0.1', 0, capture_directory=directory) as server:
            for method, path, expected in [('GET', '/list', [])]:
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
            for path in ('/delete/123', '/spectrum/123', '/spectrum/not-a-number', '/download/123', '/download/not-a-number', '/download/1/extra', '/unknown'):
                connection = HTTPConnection(*server.server_address, timeout=2)
                try:
                    connection.request('GET', path)
                    response = connection.getresponse()
                    self.assertEqual(response.status, 404)
                    response.read()
                finally:
                    connection.close()
        self.assertEqual(server.socket.fileno(), -1)

    def test_static_allowlist_content_types_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / 'web'
            assets.mkdir()
            expected = {
                '/': ('index.html', 'text/html; charset=utf-8'),
                '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                '/style.css': ('style.css', 'text/css; charset=utf-8'),
                '/spectrum-math.js': ('spectrum-math.js', 'text/javascript; charset=utf-8'),
            }
            for filename, _ in expected.values():
                (assets / filename).write_text('Test ' + filename)
            (assets / 'private.pkl').write_bytes(b'private')
            with patch('spectrometer.server.WEB_DIRECTORY', assets), \
                    running_server('127.0.0.1', 0, capture_directory=directory) as server:
                for route, (filename, content_type) in expected.items():
                    connection = HTTPConnection(*server.server_address, timeout=2)
                    try:
                        connection.request('GET', route + '?v=1')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader('Content-Type'), content_type)
                        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                        data = response.read()
                        self.assertEqual(int(response.getheader('Content-Length')), len(data))
                        self.assertEqual(data, ('Test ' + filename).encode())
                    finally:
                        connection.close()
                for route in ('/index.html', '/private.pkl', '/web/index.html',
                              '/../app.js', '/%2e%2e/app.js', '/app.js/../private.pkl',
                              '/.env', '/app.js%00'):
                    connection = HTTPConnection(*server.server_address, timeout=2)
                    try:
                        connection.request('GET', route)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 404, route)
                        response.read()
                    finally:
                        connection.close()
