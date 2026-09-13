"""Capture browsing and background loading for the small touchscreen."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import pickle

import numpy as np

from .camera import BAR_SIZE, PACKED_12_FORMATS, SENSOR_SIZE, SPECTRUM_ROI, SpectrumFrame


def load_spectrum(path):
    # These are the application's own local pickle files.
    with Path(path).open('rb') as source:
        record = pickle.load(source)
    intensity = np.asarray(record['spectrum_intensity'])
    if (intensity.shape != (3500,) or not np.issubdtype(intensity.dtype, np.number)
            or not np.all(np.isfinite(intensity))):
        raise ValueError('Invalid spectrum intensity data')
    if tuple(record.get('spectrum_roi', SPECTRUM_ROI)) != SPECTRUM_ROI:
        raise ValueError('Capture uses an unsupported spectrum region')
    bar = record.get('spectrum_bar')
    if bar is None:
        bar = raw_bar(record)
    if not isinstance(bar, bytes) or len(bar) != BAR_SIZE[0] * BAR_SIZE[1] * 3:
        raise ValueError('Invalid camera bar data')
    return SpectrumFrame(bar, intensity.copy())


def raw_bar(record):
    """Reconstruct the bar for older captures that saved only packed Bayer data."""
    import cv2

    settings = record.get('instrument_settings', {})
    config = settings.get('raw_camera_format', record.get('raw_camera_format', {}))
    fmt = str(config.get('format', ''))
    if fmt not in PACKED_12_FORMATS or tuple(config.get('size', ())) != SENSOR_SIZE:
        raise ValueError('Unsupported raw camera format')
    raw = np.asarray(record['raw_camera_output'])
    stride = int(config.get('stride', SENSOR_SIZE[0] * 3 // 2))
    if raw.dtype != np.uint8 or raw.size != SENSOR_SIZE[1] * stride:
        raise ValueError('Invalid packed camera buffer')
    x0, y0, x1, y1 = SPECTRUM_ROI
    triples = raw.reshape(SENSOR_SIZE[1], stride)[y0:y1, x0 * 3 // 2:x1 * 3 // 2].reshape(y1-y0, -1, 3)
    # For an 8-bit preview the top eight bits are already the first two bytes
    # of each CSI2 packed pair. The third byte contains the low four bits.
    bayer = np.empty((y1-y0, x1-x0), dtype=np.uint8)
    bayer[:, 0::2], bayer[:, 1::2] = triples[:, :, 0], triples[:, :, 1]
    rgb = cv2.cvtColor(bayer, getattr(cv2, 'COLOR_Bayer' + fmt[1:5] + '2RGB'))
    return cv2.resize(rgb, BAR_SIZE, interpolation=cv2.INTER_AREA).tobytes()


class ReviewList:
    ROW_HEIGHT = 42
    VISIBLE = 5

    def __init__(self, directory=None):
        self.directory = Path.home() if directory is None else Path(directory)
        self.entries = []
        self.offset = 0
        self.selected = None
        self.message = ''
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='spectrum-review')
        self.future = None
        self.loaded_path = None
        self._loading_path = None

    def refresh(self):
        self.message = ''
        try:
            def key(path):
                try:
                    return datetime.strptime(path.stem, 'spectrum-%Y-%m-%d-%H-%M-%S')
                except ValueError:
                    return datetime.min
            self.entries = sorted((p for p in self.directory.glob('spectrum-*.pkl') if p.is_file()),
                                  key=key, reverse=True)
        except OSError as exc:
            self.entries = []
            self.message = str(exc)
        self.offset = 0
        self.selected = None

    def scroll(self, rows):
        self.offset = max(0, min(max(0, len(self.entries) - self.VISIBLE), self.offset + rows))

    def display(self):
        if self.future is not None:
            return
        if self.selected is None:
            self.message = 'Select a capture first'
            return
        self.message = 'Loading capture...'
        self._loading_path = self.entries[self.selected]
        self.future = self._executor.submit(load_spectrum, self._loading_path)

    def poll(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        try:
            result = future.result()
            self.loaded_path = self._loading_path
            self.message = ''
            return result
        except Exception as exc:
            self.message = 'Cannot load: ' + str(exc)
            return None

    def delete_loaded(self):
        """Delete only the file backing the displayed spectrum, after confirmation."""
        if self.loaded_path is None:
            self.message = 'No capture is loaded'
            return False
        try:
            self.loaded_path.unlink()
        except OSError as exc:
            self.message = 'Delete failed: ' + str(exc)
            return False
        self.loaded_path = None
        offset = self.offset
        self.refresh()
        self.scroll(offset)
        return True

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
