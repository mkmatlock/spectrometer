#!/usr/bin/env python3
"""Serve a fresh JPEG at GET /capture (or /capture.jpg).

On Raspberry Pi OS, install: sudo apt install python3-picamera2
Run: python3 server.py --host 0.0.0.0 --port 8000
Capture: curl http://raspberrypi.local:8000/capture -o image.jpg

Example /etc/systemd/system/imageserv.service (adjust User and script path):

    [Unit]
    Description=Picamera2 HTTP image service
    After=network.target

    [Service]
    Type=simple
    User=pi
    SupplementaryGroups=video render
    ExecStart=/usr/bin/python3 /home/pi/Spectrometer/imageserv/server.py
    Restart=on-failure
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Enable with: sudo systemctl daemon-reload
             sudo systemctl enable --now imageserv
Logs: journalctl -u imageserv -f

Runs in the foreground for systemd supervision. There is no authentication;
use on a trusted network. Images are returned directly, without disk writes.
"""

import argparse
import io
import logging
import signal
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


LOGGER = logging.getLogger("imageserv")


def make_handler(camera):
    """Bind the shared camera to a sequential HTTP request handler."""

    class CaptureHandler(BaseHTTPRequestHandler):
        # Bound how long an idle client can occupy the single request handler.
        timeout = 10

        def do_GET(self):
            if urlsplit(self.path).path not in ("/capture", "/capture.jpg"):
                self.send_error(404, "Use /capture to capture a JPEG image")
                return

            try:
                with io.BytesIO() as buffer:
                    camera.capture_file(buffer, format="jpeg")
                    data = buffer.getvalue()
            except Exception:
                LOGGER.exception("Camera capture failed")
                self.send_error(500, "Camera capture failed")
                return

            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                LOGGER.info("Client disconnected while receiving image")

        def log_message(self, message, *args):
            LOGGER.info("%s - %s", self.client_address[0], message % args)

    return CaptureHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: all IPv4 interfaces)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Import here so --help also works on computers without camera libraries.
    from picamera2 import Picamera2

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())

    camera = Picamera2()
    try:
        camera.configure(camera.create_still_configuration(queue=False))
        camera.start(show_preview=False)
        # Allow automatic exposure and white balance to settle before serving.
        if stopping.wait(2):
            return

        # Serial requests ensure that only one capture uses the camera at a time.
        with HTTPServer((args.host, args.port), make_handler(camera)) as server:
            server.timeout = 0.5
            LOGGER.info("Serving JPEG captures on %s:%s/capture", args.host, args.port)
            while not stopping.is_set():
                server.handle_request()
    finally:
        camera.close()
        LOGGER.info("Camera closed; service stopped")


if __name__ == "__main__":
    main()
