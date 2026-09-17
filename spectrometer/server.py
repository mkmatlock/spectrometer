"""Device-hosted spectrum viewer and spectrometer REST API."""

import argparse
from copy import deepcopy
import base64
from concurrent.futures import TimeoutError
from datetime import datetime
from pathlib import Path
import pickle
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import threading
from urllib.parse import urlsplit, parse_qs

from .catalog import DuplicateSpectrumID, SpectrumCatalog, timestamp_id
from .capture import delete_capture, rename_capture, update_peak_label
from .settings import network_status


LOGGER = logging.getLogger(__name__)
WEB_DIRECTORY = Path(__file__).with_name('web')
STATIC_FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/spectrum-math.js': ('spectrum-math.js', 'text/javascript; charset=utf-8'),
    '/annotations.js': ('annotations.js', 'text/javascript; charset=utf-8'),
}
RESPONSE_CHUNK_SIZE = 64 * 1024


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(f'Cannot encode {type(value).__name__}')


def spectrum_response(path, record):
    """Full JSON representation of a saved spectrum."""
    result = dict(record, filename=path.name)
    image = record['averaged_camera_output']
    result['averaged_camera_output'] = {
        'encoding': 'base64', 'dtype': str(image.dtype), 'shape': list(image.shape),
        'data': base64.b64encode(image.tobytes()).decode('ascii')}
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
        return self.server.catalog.list_api()

    def _find_spectrum(self, spectrum_id):
        path = self.server.catalog.find(spectrum_id)
        if path is None:
            return None
        try:
            with path.open('rb') as source:
                return path, pickle.load(source)
        except FileNotFoundError:
            self.server.catalog.remove(path.name)
            return None

    def spectrum(self, spectrum_id):
        if not self.server.raw_slots.acquire(blocking=False):
            self._respond({'error': 'Another spectrum transfer is in progress'}, 503)
            return
        try:
            try:
                found = self._find_spectrum(spectrum_id)
            except DuplicateSpectrumID:
                self._respond({'error': 'Spectrum ID is ambiguous'}, 409)
                return
            except Exception:
                LOGGER.exception('Cannot load spectrum %s', spectrum_id)
                self._respond({'error': 'Cannot read spectrum data'}, 500)
                return
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
        finally:
            self.server.raw_slots.release()

    def delete_spectrum(self, spectrum_id):
        if not self.server.raw_slots.acquire(blocking=False):
            self._respond({'error': 'Another spectrum operation is in progress'}, 503)
            return
        try:
            self._delete_spectrum(spectrum_id)
        finally:
            self.server.raw_slots.release()

    def _delete_spectrum(self, spectrum_id):
        try:
            path = self.server.catalog.find(spectrum_id)
        except DuplicateSpectrumID:
            self._respond({'error': 'Spectrum ID is ambiguous'}, 409)
            return
        if path is None:
            self._respond({'error': 'Spectrum not found'}, 404)
            return
        try:
            delete_capture(path)
        except FileNotFoundError:
            self._respond({'error': 'Spectrum not found'}, 404)
        except OSError:
            LOGGER.exception('Cannot delete spectrum %s', path.name)
            self._respond({'error': 'Cannot delete spectrum'}, 500)
        else:
            self.server.catalog.remove(path.name)
            self.send_response(204)
            self.end_headers()

    def update_spectrum(self, spectrum_id):
        if self.headers.get_content_type() != 'application/json':
            self._respond({'error': 'Expected application/json'}, 415)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid body length')
            payload = json.loads(self.rfile.read(length))
            if isinstance(payload, dict) and set(payload) == {'name'}:
                name = payload['name']
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
                    raise ValueError('Invalid name')
                operation = 'rename'
            elif isinstance(payload, dict) and set(payload) == {'peak_label'}:
                annotation = payload['peak_label']
                if not isinstance(annotation, dict) or set(annotation) != {'pixel', 'label'}:
                    raise ValueError('Invalid peak label')
                pixel, label = annotation['pixel'], annotation['label']
                if type(pixel) is not int or (label is not None and
                        (not isinstance(label, str) or not 1 <= len(label.strip()) <= 64)):
                    raise ValueError('Invalid peak label')
                operation = 'label'
            else:
                raise ValueError('Invalid spectrum update')
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._respond({'error': 'Provide a name of 1–64 characters or a peak_label '
                           'with an integer pixel and a label of 1–64 characters (null to delete)'}, 400)
            return
        if not self.server.raw_slots.acquire(blocking=False):
            self._respond({'error': 'Another spectrum operation is in progress'}, 503)
            return
        try:
            try:
                path = self.server.catalog.find(spectrum_id)
            except DuplicateSpectrumID:
                self._respond({'error': 'Spectrum ID is ambiguous'}, 409)
                return
            except Exception:
                LOGGER.exception('Cannot find spectrum %s for %s', spectrum_id, operation)
                self._respond({'error': 'Cannot read spectrum metadata'}, 500)
                return
            if path is None:
                self._respond({'error': 'Spectrum not found'}, 404)
                return
            try:
                if operation == 'rename':
                    result = {'name': rename_capture(path, name)}
                else:
                    try:
                        result = {'peak_labels': update_peak_label(path, pixel, label)}
                    except ValueError as exc:
                        self._respond({'error': str(exc)}, 400)
                        return
                # Usually a metadata-only check: the mutation already updates
                # SQLite. Also refresh this instance's in-memory fallback index.
                self.server.catalog.reconcile_paths([path])
            except FileNotFoundError:
                self.server.catalog.remove(path.name)
                self._respond({'error': 'Spectrum not found'}, 404)
                return
            except Exception:
                LOGGER.exception('Cannot %s spectrum %s', operation, path.name)
                self._respond({'error': 'Cannot ' + operation + ' spectrum'}, 500)
                return
            self._respond({'id': int(spectrum_id), **result})
        finally:
            self.server.raw_slots.release()

    def settings(self):
        from .config import SettingsStore
        store = self.server.settings_store
        if store is None:
            # Standalone API reads the same persistent configuration as the UI.
            store = SettingsStore()
        result = deepcopy(store.data)
        result['network'] = dict(self.server.network_settings)
        return result

    def static_file(self, path):
        filename, content_type = STATIC_FILES[path]
        try:
            data = (WEB_DIRECTORY / filename).read_bytes()
        except OSError:
            LOGGER.exception('Cannot read web asset %s', filename)
            self._respond({'error': 'Web application unavailable'}, 500)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def _respond(self, result, status=200):
        # Serialize before committing headers so encoding failures can still
        # return a normal error response.
        data = json.dumps(result, default=json_value).encode('utf-8')
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            # SocketWriter.write uses sendall: its timeout covers the entire
            # call, not just time without progress. Large images can take
            # longer than ten seconds over Wi-Fi. Bound each write so an
            # active transfer can continue while stalled clients still time out.
            view = memoryview(data)
            for offset in range(0, len(view), RESPONSE_CHUNK_SIZE):
                self.wfile.write(view[offset:offset + RESPONSE_CHUNK_SIZE])
        except OSError as exc:
            # Headers (and perhaps part of the body) have already been sent.
            # Never append a second HTTP response to the incomplete first one.
            self.close_connection = True
            LOGGER.warning('Response interrupted for %s: %s',
                           urlsplit(self.path).path, type(exc).__name__)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            self.static_file(path)
            return
        if path == '/capture':
            self.capture()
            return
        endpoints = {'/list': self.list,
                     '/settings': self.settings}
        if path in endpoints:
            try:
                self._respond(endpoints[path]())
            except Exception:
                LOGGER.exception('API request failed for %s', path)
                self._respond({'error': 'Request failed'}, 500)
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

    def do_PATCH(self):
        path = urlsplit(self.path).path
        if match := re.fullmatch(r'/spectrum/([0-9]+)', path):
            self.update_spectrum(match.group(1))
        else:
            self._respond({'error': 'Not found'}, 404)

    def log_message(self, message, *args):
        LOGGER.info('%s - %s', self.client_address[0], message % args)


@contextmanager
def running_server(host='0.0.0.0', port=8000, camera=None, capture_directory=None, settings_store=None):
    """Serve alongside the UI, releasing the socket when the application exits."""
    with ThreadingHTTPServer((host, port), APIHandler) as server:
        server.settings_store = settings_store
        server.camera = camera
        server.capture_directory = Path(capture_directory if capture_directory is not None else
                                        getattr(camera, "_capture_directory", Path.home()))
        server.catalog = SpectrumCatalog(server.capture_directory, persistent=True)
        # Index captures before accepting requests. Thereafter the
        # request path is a metadata-only SQLite query.
        server.catalog.reconcile_all()
        # Fault the small catalog and JSON encoder into memory before the
        # full-resolution camera begins consuming the single CPU.
        json.dumps(server.catalog.list_api())
        server.raw_slots = threading.BoundedSemaphore(1)
        server.network_settings = {'ip_address': 'Loading...', 'wifi_ssid': 'Loading...'}
        reconcile_stop = threading.Event()

        def reconcile_catalog():
            while not reconcile_stop.is_set():
                try:
                    address, ssid = network_status()
                    server.network_settings = {'ip_address': address, 'wifi_ssid': ssid}
                except Exception:
                    LOGGER.exception('Cannot read network status')
                if reconcile_stop.wait(30):
                    return
                try:
                    server.catalog.reconcile_all()
                except Exception:
                    LOGGER.exception('Background spectrum reconciliation failed')

        reconciler = threading.Thread(target=reconcile_catalog, name='spectrum-catalog', daemon=True)
        reconciler.start()
        worker = threading.Thread(target=server.serve_forever, name='spectrometer-api', daemon=True)
        worker.start()
        # Prime ThreadingHTTPServer's first handler thread before camera work
        # saturates the Pi Zero. Later external clients avoid a one-off delay.
        warm_host = '127.0.0.1' if server.server_address[0] in ('', '0.0.0.0') else server.server_address[0]
        connection = HTTPConnection(warm_host, server.server_address[1], timeout=5)
        try:
            connection.request('GET', '/list')
            response = connection.getresponse()
            response.read()
            if response.status != 200:
                LOGGER.warning('API warmup returned HTTP %s', response.status)
        except OSError:
            LOGGER.exception('API warmup failed')
        finally:
            connection.close()
        LOGGER.info('Spectrometer API listening on %s:%s', *server.server_address)
        try:
            yield server
        finally:
            server.shutdown()
            worker.join()
            reconcile_stop.set()
            reconciler.join()
            server.catalog.close()


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
