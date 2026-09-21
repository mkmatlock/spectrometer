"""Validated, atomic persistence for user settings (plain dictionaries in pickle)."""

from copy import deepcopy
import math
import os
from pathlib import Path
import pickle
import tempfile


DEFAULTS = {
    'camera': {'frame_rate': 5.0, 'resolution': (4056, 3040), 'exposure_us': None,
               'frame_averaging': 3},
    'calibration': {'scale': {}, 'sensor_area': (0, 1550, 3500, 1800),
                    'channel_ranges': {}},
}

# Native 12-bit IMX477 sensor modes.  The 1332x990 mode is 10-bit and is not
# compatible with this application's packed 12-bit capture path.
CAMERA_MODES = (
    ((4056, 3040), 10),
    ((2028, 1520), 40),
    ((2028, 1080), 50),
)


def maximum_frame_rate(resolution):
    return next((fps for size, fps in CAMERA_MODES if tuple(resolution) == size), 10)


def validate(data):
    camera, calibration = data['camera'], data['calibration']
    fps, exposure = camera['frame_rate'], camera['exposure_us']
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Frame rate must be positive and finite')
    if exposure not in (None, 'min', 'max') and (type(exposure) is not int or exposure <= 0):
        raise ValueError('Exposure must be positive integer microseconds, min, max, or None')
    averaging = camera['frame_averaging']
    if type(averaging) is not int or not 1 <= averaging <= 10:
        raise ValueError('Frame averaging must be an integer from 1 to 10')
    size, roi = camera['resolution'], calibration['sensor_area']
    if (not isinstance(size, (tuple, list)) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
            or size[0] > 4056 or size[1] > 3040):
        raise ValueError('Invalid camera resolution')
    if (not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(v) is not int or v % 2 for v in roi)
            or not 0 <= roi[0] < roi[2] <= size[0]
            or not 0 <= roi[1] < roi[3] <= size[1] or roi[2] - roi[0] < 462):
        raise ValueError('Sensor area must be an even-aligned bounding box inside the image, at least 462 pixels wide')
    if (roi[2] - roi[0]) * (roi[3] - roi[1]) * 3 * averaging > 128 * 1024 * 1024:
        raise ValueError('Frame averaging window is too large for the sensor area')
    if not isinstance(calibration['scale'], dict):
        raise ValueError('Scale calibration must be a pixel/value dictionary')
    for pixel, value in calibration['scale'].items():
        if (type(pixel) is not int or not 0 <= pixel < size[0]
                or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError('Invalid scale calibration pair')
    ranges = calibration['channel_ranges']
    if not isinstance(ranges, dict) or any(name not in ('Red', 'Green', 'Blue') for name in ranges):
        raise ValueError('Channel ranges must contain only Red, Green, and Blue')
    for extent in ranges.values():
        if (not isinstance(extent, (tuple, list)) or len(extent) != 2
                or any(type(v) is not int for v in extent)
                or not 0 <= extent[0] <= extent[1] < size[0]):
            raise ValueError('Channel ranges must be pixel pairs inside the camera image')


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path.home() / '.spectrometer_config' if path is None else Path(path)
        if self.path.exists():
            try:
                with self.path.open('rb') as source:
                    self.data = pickle.load(source)
                validate(self.data)
            except Exception as exc:
                raise ValueError(f'Cannot load settings from {self.path}: {exc}') from exc
        else:
            self.data = deepcopy(DEFAULTS)
            self._write(self.data)

    def update(self, *, camera=None, calibration=None):
        updated = deepcopy(self.data)
        for name, changes in (('camera', camera), ('calibration', calibration)):
            if changes is not None:
                updated[name].update(deepcopy(changes))
        validate(updated)
        if updated != self.data:
            self._write(updated)
            self.data = updated

    def _write(self, data):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.spectrometer-config-', delete=False) as output:
                temporary = Path(output.name)
                pickle.dump(data, output, protocol=pickle.HIGHEST_PROTOCOL)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
