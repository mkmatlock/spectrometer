"""Spectrometer REST API with named capture; other endpoints remain stubs."""

import argparse
import base64
from concurrent.futures import TimeoutError
from datetime import datetime
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import threading
from urllib.parse import urlsplit, parse_qs


LOGGER = logging.getLogger(__name__)


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(f'Cannot encode {type(value).__name__}')


class APIHandler(BaseHTTPRequestHandler):
    timeout = 10

    def capture(self):
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        names = query.get('name', [])
        if set(query) != {'name'} or len(names) != 1 or not 1 <= len(names[0].strip()) <= 64:
            self._respond({'error': 'Provide one name of 1–64 characters'}, 400)
            return
        camera = self.server.camera
        if camera is None:
            self._respond({'error': 'Camera unavailable'}, 503)
            return
        future = camera.request_api_capture(names[0].strip())
        if future is None:
            self._respond({'error': 'Camera paused or capture busy'}, 409)
            return
        try:
            path, record = future.result(timeout=120)
            result = dict(record, filename=path.name)
            raw = record['raw_camera_output']
            result['raw_camera_output'] = {
                'encoding': 'base64', 'dtype': str(raw.dtype), 'shape': list(raw.shape),
                'data': base64.b64encode(raw.tobytes()).decode('ascii')}
            self._respond(result)
        except TimeoutError:
            self._respond({'error': 'Capture timed out; it may still finish saving'}, 504)
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info('Capture saved; client disconnected')
        except Exception:
            LOGGER.exception('API capture failed')
            self._respond({'error': 'Capture failed'}, 500)

    def list(self):
        return []

    def download(self, spectrum_id):
        return {}
    
    def delete(self, spectrum_id):
        return {}

    def settings(self):
        return {}

    def _respond(self, result, status=200):
        data = json.dumps(result, default=json_value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/capture':
            self.capture()
            return
        endpoints = {'/list': self.list,
                     '/settings': self.settings}
        if path in endpoints:
            self._respond(endpoints[path]())
        elif match := re.fullmatch(r'/download/([0-9]+)', path):
            self._respond(self.download(match.group(1)))
        elif match := re.fullmatch(r'/delete/([0-9]+)', path):
            self._respond(self.delete(match.group(1)))
        else:
            self._respond({'error': 'Not found'}, 404)

    def log_message(self, message, *args):
        LOGGER.info('%s - %s', self.client_address[0], message % args)


@contextmanager
def running_server(host='0.0.0.0', port=8000, camera=None):
    """Serve alongside the UI, releasing the socket when the application exits."""
    with ThreadingHTTPServer((host, port), APIHandler) as server:
        server.camera = camera
        worker = threading.Thread(target=server.serve_forever, name='spectrometer-api', daemon=True)
        worker.start()
        LOGGER.info('Spectrometer API listening on %s:%s', *server.server_address)
        try:
            yield server
        finally:
            server.shutdown()
            worker.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    import signal
    stopping = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    logging.basicConfig(level=logging.INFO)
    with running_server(args.host, args.port):
        stopping.wait()


if __name__ == '__main__':
    main()
