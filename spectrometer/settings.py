"""Read-only settings snapshots and bounded background network queries."""

from concurrent.futures import ThreadPoolExecutor
import subprocess


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
        fps, size, exposure = (snapshot.get(key) for key in ('frame_rate', 'resolution', 'exposure_us'))
        self.rows = [
            ('Frame rate', f'{fps:g} fps' if fps is not None else 'Unavailable'),
            ('Camera resolution', f'{size[0]} x {size[1]}' if size else 'Unavailable'),
            ('Exposure time', f'{exposure / 1000:g} ms' if exposure is not None
             else ('Auto (pending)' if snapshot else 'Unavailable')),
            ('IP address', 'Loading...'),
            ('Wi-Fi network', 'Loading...'),
        ]
        self.message = ''
        if self.future is None or self.future.done():
            self.future = self._executor.submit(network_status)

    def poll(self):
        if self.future is None or not self.future.done():
            return False
        try:
            address, network = self.future.result()
        except Exception:
            address = network = 'Unavailable'
        self.future = None
        self.rows[3:] = [('IP address', address), ('Wi-Fi network', network)]
        return True

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
