"""Spectrometer REST API with named capture; other endpoints remain stubs."""

import argparse
import base64
from concurrent.futures import TimeoutError
from datetime import datetime, timezone
from pathlib import Path
import pickle
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


def timestamp_id(timestamp):
    elapsed = timestamp.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 1000 + elapsed.microseconds // 1000


def spectrum_response(path, record):
    """Full JSON representation of a saved spectrum."""
    result = dict(record, filename=path.name)
    raw = record['raw_camera_output']
    result['raw_camera_output'] = {
        'encoding': 'base64', 'dtype': str(raw.dtype), 'shape': list(raw.shape),
        'data': base64.b64encode(raw.tobytes()).decode('ascii')}
    return result


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
            _, record = future.result(timeout=120)
            self._respond(timestamp_id(record["timestamp"]))
        except TimeoutError:
            self._respond({'error': 'Capture timed out; it may still finish saving'}, 504)
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info('Capture saved; client disconnected')
        except Exception:
            LOGGER.exception('API capture failed')
            self._respond({'error': 'Capture failed'}, 500)

    def list(self):
        from .review import record_calibration
        result = []
        for path in self.server.capture_directory.glob('spectrum-*.pkl'):
            if not path.is_file():
                continue
            try:
                with path.open('rb') as source:
                    record = pickle.load(source)
                timestamp = record['timestamp']
                spectrum_id = timestamp_id(timestamp)
                name = record.get('name', '')
                entry = {
                    'id': spectrum_id,
                    'name': name.strip() if isinstance(name, str) and name.strip() else 'Unnamed spectrum',
                    'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                }
                # Release the full-resolution camera buffer before the next file.
                del record
                json.dumps(entry, default=json_value, allow_nan=False)
                result.append(entry)
            except Exception:
                LOGGER.exception('Cannot list spectrum %s', path.name)
        return sorted(result, key=lambda entry: entry['id'], reverse=True)

    def _find_spectrum(self, spectrum_id):
        wanted = int(spectrum_id)
        for path in self.server.capture_directory.glob('spectrum-*.pkl'):
            if not path.is_file():
                continue
            try:
                with path.open('rb') as source:
                    record = pickle.load(source)
                matches = timestamp_id(record['timestamp']) == wanted
            except Exception:
                LOGGER.exception('Cannot read spectrum %s', path.name)
                continue
            if not matches:
                del record
                continue
            return path, record
        return None

    def spectrum(self, spectrum_id):
        found = self._find_spectrum(spectrum_id)
        if found is None:
            self._respond({'error': 'Spectrum not found'}, 404)
            return
        path, record = found
        try:
            self._respond(spectrum_response(path, record))
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info('Client disconnected during download')
        except Exception:
            LOGGER.exception('Cannot read spectrum %s', path.name)
            self._respond({'error': 'Cannot read spectrum data'}, 500)

    def delete_spectrum(self, spectrum_id):
        found = self._find_spectrum(spectrum_id)
        if found is None:
            self._respond({'error': 'Spectrum not found'}, 404)
            return
        path, _ = found
        try:
            path.unlink()
        except FileNotFoundError:
            self._respond({'error': 'Spectrum not found'}, 404)
        except OSError:
            LOGGER.exception('Cannot delete spectrum %s', path.name)
            self._respond({'error': 'Cannot delete spectrum'}, 500)
        else:
            self.send_response(204)
            self.end_headers()

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
        elif match := re.fullmatch(r'/spectrum/([0-9]+)', path):
            self.spectrum(match.group(1))
        else:
            self._respond({'error': 'Not found'}, 404)

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if match := re.fullmatch(r'/spectrum/([0-9]+)', path):
            self.delete_spectrum(match.group(1))
        else:
            self._respond({'error': 'Not found'}, 404)

    def log_message(self, message, *args):
        LOGGER.info('%s - %s', self.client_address[0], message % args)


@contextmanager
def running_server(host='0.0.0.0', port=8000, camera=None, capture_directory=None):
    """Serve alongside the UI, releasing the socket when the application exits."""
    with ThreadingHTTPServer((host, port), APIHandler) as server:
        server.camera = camera
        server.capture_directory = Path(capture_directory if capture_directory is not None else
                                        getattr(camera, "_capture_directory", Path.home()))
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
