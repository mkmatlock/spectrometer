"""Validated, atomic persistence for user settings (plain dictionaries in pickle)."""

from copy import deepcopy
import math
import os
from pathlib import Path
import pickle
import tempfile


DEFAULTS = {
    'camera': {'frame_rate': 5.0, 'resolution': (4056, 3040), 'exposure_us': None},
    'calibration': {'scale': {}, 'sensor_area': (0, 1550, 3500, 1800)},
}


def validate(data):
    camera, calibration = data['camera'], data['calibration']
    fps, exposure = camera['frame_rate'], camera['exposure_us']
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Frame rate must be positive and finite')
    if exposure is not None and (type(exposure) is not int or exposure <= 0):
        raise ValueError('Exposure must be positive integer microseconds or None')
    size, roi = camera['resolution'], calibration['sensor_area']
    if (not isinstance(size, (tuple, list)) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size)
            or size[0] > 4056 or size[1] > 3040):
        raise ValueError('Invalid camera resolution')
    if (not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(v) is not int or v % 2 for v in roi)
            or not 0 <= roi[0] < roi[2] <= size[0]
            or not 0 <= roi[1] < roi[3] <= size[1] or roi[2] - roi[0] < 462):
        raise ValueError('Sensor area must be an even-aligned bounding box inside the image, at least 462 pixels wide')
    if not isinstance(calibration['scale'], dict):
        raise ValueError('Scale calibration must be a pixel/value dictionary')
    for pixel, value in calibration['scale'].items():
        if (type(pixel) is not int or not 0 <= pixel < size[0]
                or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError('Invalid scale calibration pair')


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path.home() / '.spectrometer_config' if path is None else Path(path)
        self.data = deepcopy(DEFAULTS)
        if self.path.exists():
            try:
                with self.path.open('rb') as source:
                    saved = pickle.load(source)
                for section in DEFAULTS:
                    self.data[section].update(saved.get(section, {}))
                validate(self.data)
            except Exception as exc:
                raise ValueError(f'Cannot load settings from {self.path}: {exc}') from exc
        else:
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
