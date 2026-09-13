"""Minimal spectrometer REST API; all endpoints currently return empty results."""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import threading
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)


class APIHandler(BaseHTTPRequestHandler):
    timeout = 10

    def capture(self):
        return {}

    def list_spectra(self):
        return []

    def download(self, spectrum_number):
        return {}

    def settings(self):
        return {}

    def _respond(self, result, status=200):
        data = json.dumps(result).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        endpoints = {'/capture': self.capture, '/list': self.list_spectra,
                     '/settings': self.settings}
        if path in endpoints:
            self._respond(endpoints[path]())
        elif match := re.fullmatch(r'/download/([0-9]+)', path):
            self._respond(self.download(match.group(1)))
        else:
            self._respond({'error': 'Not found'}, 404)

    def do_POST(self):
        if urlsplit(self.path).path == '/capture':
            self._respond(self.capture())
        else:
            self._respond({'error': 'Method not allowed'}, 405)

    def log_message(self, message, *args):
        LOGGER.info('%s - %s', self.client_address[0], message % args)


@contextmanager
def running_server(host='0.0.0.0', port=8000):
    """Serve alongside the UI, releasing the socket when the application exits."""
    with ThreadingHTTPServer((host, port), APIHandler) as server:
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
