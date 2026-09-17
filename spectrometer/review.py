"""Capture browsing and background loading for the small touchscreen."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import pickle

import numpy as np

from .camera import BAR_SIZE, SENSOR_SIZE, SpectrumFrame
from .catalog import SpectrumCatalog


def record_calibration(record):
    """Return the calibration saved with this capture."""
    return deepcopy(record['instrument_settings']['calibration_settings'])


def record_roi(record):
    roi = tuple(record['spectrum_roi'])
    if (len(roi) != 4 or any(type(v) is not int or v % 2 for v in roi)
            or not 0 <= roi[0] < roi[2] <= SENSOR_SIZE[0]
            or not 0 <= roi[1] < roi[3] <= SENSOR_SIZE[1]
            or roi[2] - roi[0] < BAR_SIZE[0]):
        raise ValueError('Invalid sensor area')
    return roi


def load_spectrum(path):
    # These are the application's own local pickle files.
    with Path(path).open('rb') as source:
        record = pickle.load(source)
    roi = record_roi(record)
    intensity = np.asarray(record['spectrum_intensity'])
    if (intensity.shape != (roi[2] - roi[0],) or not np.issubdtype(intensity.dtype, np.number)
            or not np.all(np.isfinite(intensity))):
        raise ValueError('Invalid spectrum intensity data')
    bar = record['spectrum_bar']
    if not isinstance(bar, bytes) or len(bar) != BAR_SIZE[0] * BAR_SIZE[1] * 3:
        raise ValueError('Invalid camera bar data')
    maximum = record['spectrum_maximum']
    if (not isinstance(maximum, (int, float)) or isinstance(maximum, bool)
            or not np.isfinite(maximum) or maximum <= 0):
        raise ValueError('Invalid spectrum maximum')
    return SpectrumFrame(bar, intensity.copy(), roi, record_calibration(record), maximum)


def raw_rgb(record):
    """Decode the saved averaged BGR ROI for display and channel processing."""
    import cv2

    config = record['instrument_settings']['averaged_camera_format']
    roi = record_roi(record)
    size, origin = tuple(config['size']), tuple(config['origin'])
    image = np.asarray(record['averaged_camera_output'])
    if (config['format'] != 'BGR888' or size != (roi[2] - roi[0], roi[3] - roi[1])
            or origin != roi[:2] or image.dtype != np.uint8
            or image.shape != (size[1], size[0], 3)):
        raise ValueError('Invalid averaged camera image')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def load_channels(path):
    """Build display-only channel views from the full ROI, never the tiny bar."""
    import cv2
    with Path(path).open('rb') as source:
        record = pickle.load(source)
    roi = record_roi(record)
    rgb = raw_rgb(record)
    preview = cv2.resize(rgb, BAR_SIZE, interpolation=cv2.INTER_AREA)
    result = {}
    for index, channel in enumerate(('Red', 'Green', 'Blue')):
        totals = cv2.reduce(np.ascontiguousarray(rgb[:, :, index]), 0,
                            cv2.REDUCE_SUM, dtype=cv2.CV_32S).reshape(-1)
        bar = np.zeros_like(preview)
        bar[:, :, index] = preview[:, :, index]
        result[channel] = SpectrumFrame(bar.tobytes(), totals, roi,
                                        record_calibration(record), (roi[3] - roi[1]) * 255)
    return result


class ReviewList:
    ROW_HEIGHT = 42
    VISIBLE = 5

    def __init__(self, directory=None):
        self.directory = Path.home() if directory is None else Path(directory)
        self.catalog = SpectrumCatalog(self.directory)
        self.entries = []
        self.names = {}
        self.timestamps = {}
        self._names_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='spectrum-names')
        self._names_future = None
        self._reconcile_queue = []
        self.offset = 0
        self.selected = None
        self.message = ''
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='spectrum-review')
        self.future = None
        self.loaded_path = None
        self._loading_path = None
        self.filter_future = None
        self.filter_channel = ('Red', 'Green', 'Blue')
        self._wanted_channel = None
        self._channel_cache = {}
        self.blackbody_future = None
        self.blackbody_error = None

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
            indexed = self.catalog.row_map()
            self.names = {path: indexed[path.name]['name'] for path in self.entries
                          if path.name in indexed}
            self.timestamps = {path: indexed[path.name]['display_timestamp']
                               for path in self.entries if path.name in indexed}
            missing = [path for path in self.entries if path not in self.names]
            visible = set(self.entries[:self.VISIBLE])
            self._reconcile_queue = ([path for path in missing if path in visible]
                                     + [path for path in missing if path not in visible])
        except OSError as exc:
            self.entries = []
            self.message = str(exc)
        self.offset = 0
        self.selected = None

    def poll_names(self):
        changed = False
        if self._names_future is not None and self._names_future.done():
            try:
                self._names_future.result()
                indexed = self.catalog.row_map()
                self.names.update({path: indexed[path.name]['name'] for path in self.entries
                                   if path.name in indexed})
                self.timestamps.update({path: indexed[path.name]['display_timestamp']
                                        for path in self.entries if path.name in indexed})
            except Exception as exc:
                self.message = 'Cannot load names: ' + str(exc)
            self._names_future = None
            changed = True
        if self._names_future is None:
            visible = [path for path in self.entries[self.offset:self.offset + self.VISIBLE]
                       if path not in self.names and path in self._reconcile_queue]
            if visible:
                batch = visible[:1]
            else:
                batch = self._reconcile_queue[:1]
            if batch:
                chosen = set(batch)
                self._reconcile_queue = [path for path in self._reconcile_queue
                                         if path not in chosen]
                self._names_future = self._names_executor.submit(self.catalog.reconcile_paths, batch)
        return changed

    def rename(self, path, name):
        from .capture import rename_capture
        # Serialize with metadata indexing so a stale indexing job cannot
        # overwrite the newly saved name.
        return self._names_executor.submit(rename_capture, path, name)

    def save_scale(self, labels):
        """Save the completed scale to the displayed capture off the UI thread."""
        from .capture import update_scale_calibration
        if self.loaded_path is None:
            raise ValueError('No capture is loaded')
        return self._names_executor.submit(update_scale_calibration,
                                           self.loaded_path, deepcopy(labels))

    def read_name(self, path):
        def read():
            from .catalog import display_name
            with path.open('rb') as source:
                return display_name(pickle.load(source))
        return self._names_executor.submit(read)

    def name(self, path):
        return self.names.get(path, 'Loading name...') if path else 'Unnamed spectrum'

    def timestamp(self, path):
        return self.timestamps.get(path, '') if path else ''

    def scroll(self, rows):
        self.offset = max(0, min(max(0, len(self.entries) - self.VISIBLE), self.offset + rows))

    def display(self, loader=load_spectrum):
        if self.future is not None:
            return
        if self.selected is None:
            self.message = 'Select a capture first'
            return
        self.message = 'Loading capture...'
        self._loading_path = self.entries[self.selected]
        self.future = self._executor.submit(loader, self._loading_path)

    def poll(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        try:
            result = future.result()
            self.loaded_path = self._loading_path
            if isinstance(result, dict):
                self._channel_cache = {}
            else:
                self._channel_cache = {'All': result}
                self.filter_channel = ('Red', 'Green', 'Blue')
            self.cancel_filter()
            self.message = ''
            return result
        except Exception as exc:
            self.message = 'Cannot load: ' + str(exc)
            return None

    def cancel_filter(self):
        if self.filter_future is not None:
            self.filter_future.cancel()
            self.filter_future = None
        self._wanted_channel = None

    def request_blackbody(self, intensity, roi, scale):
        from .blackbody import fit_blackbody
        self.cancel_blackbody()
        self.blackbody_error = None
        self.blackbody_future = self._executor.submit(
            fit_blackbody, np.asarray(intensity).copy(), tuple(roi), scale)

    def poll_blackbody(self):
        if self.blackbody_future is None or not self.blackbody_future.done():
            return None
        future, self.blackbody_future = self.blackbody_future, None
        try:
            return future.result()
        except Exception as exc:
            self.blackbody_error = str(exc)
            return None

    def cancel_blackbody(self):
        if self.blackbody_future is not None:
            self.blackbody_future.cancel()
            self.blackbody_future = None

    def _channel_frame(self, channels):
        original = self._channel_cache['All']
        if len(channels) == 3:
            return original
        if not channels:
            return SpectrumFrame(bytes(len(original.bar)), np.zeros_like(original.intensity),
                                 original.roi, original.calibration, original.maximum)
        frames = [self._channel_cache[name] for name in channels]
        # Match live processing by summing every enabled channel. Preview bytes
        # occupy disjoint RGB components, so their sum cannot overflow uint8.
        totals = sum((frame.intensity for frame in frames),
                     start=np.zeros_like(original.intensity))
        bar = sum(np.frombuffer(frame.bar, np.uint8) for frame in frames).tobytes()
        maximum = (original.roi[3] - original.roi[1]) * 255 * len(frames)
        return SpectrumFrame(bar, totals, original.roi, original.calibration, maximum)

    def request_channels(self, channels):
        if len(set(channels)) != len(channels) or any(name not in ('Red', 'Green', 'Blue') for name in channels):
            raise ValueError('Unknown or duplicate channel')
        self._wanted_channel = tuple(channels)
        self.message = ''
        if len(channels) in (0, 3) or all(name in self._channel_cache for name in channels):
            self.filter_channel = self._wanted_channel
            return self._channel_frame(channels)
        if self.filter_future is None:
            self.filter_future = self._executor.submit(load_channels, self.loaded_path)
        self.message = 'Loading channels...'
        return None

    def poll_filter(self):
        if self.filter_future is None or not self.filter_future.done():
            return None
        future, self.filter_future = self.filter_future, None
        try:
            self._channel_cache.update(future.result())
            self.message = ''
            self.filter_channel = self._wanted_channel
            return self._channel_frame(self.filter_channel)
        except Exception as exc:
            self.message = 'Cannot filter: ' + str(exc)
            return None

    def delete_loaded(self):
        """Delete only the file backing the displayed spectrum, after confirmation."""
        if self.loaded_path is None:
            self.message = 'No capture is loaded'
            return False
        path = self.loaded_path
        try:
            from .capture import delete_capture
            delete_capture(path)
        except OSError as exc:
            self.message = 'Delete failed: ' + str(exc)
            return False
        self.loaded_path = None
        self.catalog.remove(path.name)
        offset = self.offset
        self.refresh()
        self.scroll(offset)
        return True

    def close(self):
        self.cancel_blackbody()
        self._names_executor.shutdown(wait=True, cancel_futures=True)
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.catalog.close()
