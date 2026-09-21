"""Settings display snapshots and bounded background network queries."""

from concurrent.futures import ThreadPoolExecutor
import subprocess


def frame_rate_label(fps):
    return f'{1 / fps:g} spf' if fps < 1 else f'{fps:g} fps'


def exposure_label(exposure):
    if exposure in ('min', 'max'):
        return exposure.title()
    return 'Auto' if exposure is None else f'{exposure / 1000:g} ms'


def slider_frame_rate(value):
    """Slider positions -3..0 are 5..2 seconds per frame; 1+ are fps."""
    return 1 / (2 - value) if value < 1 else value


def frame_rate_position(fps):
    return round(2 - 1 / fps) if fps < 1 else round(fps)


def _output(command):
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def network_status():
    addresses = _output(['hostname', '-I'])
    address = next((ip for ip in (addresses or '').split() if ':' not in ip), None)
    if address is None:
        address = next(iter((addresses or '').split()), 'Unavailable')
    ssid = _output(['iwgetid', '--raw'])
    if not ssid:
        # NetworkManager fallback; no scan is requested.
        networks = _output(['nmcli', '--escape', 'no', '-t', '-f', 'IN-USE,SSID',
                            'device', 'wifi', 'list', '--rescan', 'no'])
        ssid = next((line[2:] for line in (networks or '').splitlines()
                     if line.startswith('*:')), None)
    return address, ssid or 'Not connected / unavailable'


class SettingsView:
    def __init__(self):
        self.rows = []
        self.message = ''
        self.future = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='settings-network')

    def open(self, snapshot):
        self.rows = self.camera_rows(snapshot) + [
            ('IP address', 'Loading...'),
            ('Wi-Fi network', 'Loading...'),
        ]
        self.message = ''
        if self.future is None or self.future.done():
            self.future = self._executor.submit(network_status)

    @staticmethod
    def camera_rows(snapshot):
        fps, size, exposure, averaging = (snapshot[key] for key in
                                          ('frame_rate', 'resolution', 'exposure_us', 'frame_averaging'))
        return [
            ('Frame rate', frame_rate_label(fps)),
            ('Camera resolution', f'{size[0]} x {size[1]}'),
            ('Exposure time', exposure_label(exposure)),
            ('Frame averaging', str(averaging)),
        ]

    def update_camera(self, snapshot):
        network = self.rows[4:] if len(self.rows) >= 6 else [
            ('IP address', 'Loading...'), ('Wi-Fi network', 'Loading...')]
        self.rows = self.camera_rows(snapshot) + network

    def poll(self):
        if self.future is None or not self.future.done():
            return False
        try:
            address, network = self.future.result()
        except Exception:
            address = network = 'Unavailable'
        self.future = None
        self.rows[4:] = [('IP address', address), ('Wi-Fi network', network)]
        return True

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
