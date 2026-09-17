"""Full-resolution IMX477 acquisition with an OpenCV spectrum-bar preview."""

from dataclasses import dataclass, replace, field
from collections import deque
from copy import deepcopy
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from pathlib import Path

from .performance import PerformanceMetrics


LOGGER = logging.getLogger(__name__)
METRICS = PerformanceMetrics('camera')
SENSOR_SIZE = (4056, 3040)
SPECTRUM_ROI = (0, 1550, 3500, 1800)  # x0, y0, x1, y1 (exclusive)
BAR_SIZE = (462, 38)  # Inside the UI's one-pixel border.
PACKED_12_FORMATS = {"S%s12_CSI2P" % order for order in
                     ("RGGB", "GRBG", "GBRG", "BGGR")}


@dataclass(frozen=True)
class CameraSettings:
    frame_rate: float = 5.0
    exposure_us: int = None  # None keeps automatic exposure enabled.
    resolution: tuple = SENSOR_SIZE
    roi: tuple = SPECTRUM_ROI
    frame_averaging: int = 3

    def __post_init__(self):
        if not math.isfinite(self.frame_rate) or self.frame_rate <= 0:
            raise ValueError("Frame rate must be positive and finite")
        if self.exposure_us is not None and self.exposure_us <= 0:
            raise ValueError("Exposure must be a positive number of microseconds")
        if type(self.frame_averaging) is not int or not 1 <= self.frame_averaging <= 10:
            raise ValueError("Frame averaging must be an integer from 1 to 10")
        roi_bytes = (self.roi[2] - self.roi[0]) * (self.roi[3] - self.roi[1]) * 3
        if roi_bytes * self.frame_averaging > 128 * 1024 * 1024:
            raise ValueError("Frame averaging window is too large for the sensor area")


def _workspace_array(workspace, name, shape, dtype):
    import numpy as np
    if workspace is None:
        return np.empty(shape, dtype=dtype)
    value = workspace.get(name)
    if value is None or value.shape != shape or value.dtype != np.dtype(dtype):
        value = workspace[name] = np.empty(shape, dtype=dtype)
    return value


def spectrum_bar(frame, roi=SPECTRUM_ROI, resolution=SENSOR_SIZE, workspace=None):
    """Crop full-resolution BGR pixels before resizing and converting to RGB."""
    import cv2

    if frame.shape != (resolution[1], resolution[0], 3):
        raise ValueError(f"Expected a {resolution[0]}x{resolution[1]} three-channel camera frame")
    x0, y0, x1, y1 = roi
    crop = frame[y0:y1, x0:x1]
    started = time.monotonic()
    # This is a display-only thumbnail. Linear sampling is substantially less
    # expensive than area resampling on the Pi Zero and does not affect the
    # full-resolution channel sums used or saved as the spectrum.
    preview = _workspace_array(workspace, 'preview', (BAR_SIZE[1], BAR_SIZE[0], 3), 'uint8')
    cv2.resize(crop, BAR_SIZE, dst=preview, interpolation=cv2.INTER_LINEAR)
    METRICS.add('bar_resize_ms', (time.monotonic() - started) * 1000)
    started = time.monotonic()
    rgb = _workspace_array(workspace, 'preview_rgb', preview.shape, 'uint8')
    cv2.cvtColor(preview, cv2.COLOR_BGR2RGB, dst=rgb)
    result = rgb.tobytes()
    METRICS.add('bar_convert_ms', (time.monotonic() - started) * 1000)
    return result


@dataclass(frozen=True)
class SpectrumFrame:
    bar: bytes
    intensity: object  # Owned int32 array, one total per sensor column.
    roi: tuple = SPECTRUM_ROI
    calibration: dict = field(default_factory=dict)
    maximum: int = None
    peak_labels: dict = field(default_factory=dict)


class FrameAverager:
    """Exact rolling average of ROI images, previews, and spectrum totals."""

    def __init__(self, count):
        self.count = count
        self.reset()

    def reset(self):
        self.frames = deque()
        self.bar_sum = None
        self.intensity_sum = None

    def add(self, frame, bgr_image):
        import numpy as np

        image = np.ascontiguousarray(bgr_image).copy()
        bar = np.frombuffer(frame.bar, np.uint8).copy()
        intensity = np.asarray(frame.intensity, dtype=np.int32).copy()
        if self.frames and self.frames[0][0].shape != image.shape:
            self.reset()
        if self.bar_sum is None:
            self.bar_sum = np.zeros(bar.shape, np.uint16)
            self.intensity_sum = np.zeros(intensity.shape, np.int64)
        if len(self.frames) == self.count:
            old_image, old_bar, old_intensity = self.frames.popleft()
            self.bar_sum -= old_bar
            self.intensity_sum -= old_intensity
        self.frames.append((image, bar, intensity))
        self.bar_sum += bar
        self.intensity_sum += intensity
        if len(self.frames) < self.count:
            return None
        averaged_bar = np.floor_divide(
            self.bar_sum + self.count // 2, self.count).astype(np.uint8).tobytes()
        averaged_intensity = np.floor_divide(
            self.intensity_sum + self.count // 2, self.count).astype(np.int32)
        return SpectrumFrame(averaged_bar, averaged_intensity, frame.roi,
                             frame.calibration, frame.maximum)

    def image(self):
        import numpy as np
        if len(self.frames) < self.count:
            return None
        shape = self.frames[0][0].shape
        averaged = np.empty(shape, np.uint8)
        # Accumulate a few rows at a time so a full-resolution ROI does not
        # require another full uint16 image alongside the rolling window.
        for start in range(0, shape[0], 16):
            end = min(shape[0], start + 16)
            total = np.zeros((end - start, shape[1], shape[2]), np.uint16)
            for image, _, _ in self.frames:
                total += image[start:end]
            averaged[start:end] = np.floor_divide(
                total + self.count // 2, self.count).astype(np.uint8)
        return averaged


def process_frame(frame, roi=SPECTRUM_ROI, resolution=SENSOR_SIZE, workspace=None,
                  channel_ranges=None):
    """Sum enabled B, G, and R values vertically in the original ROI."""
    import cv2

    bar = spectrum_bar(frame, roi, resolution, workspace)
    x0, y0, x1, y1 = roi
    started = time.monotonic()
    width = x1 - x0
    channel_totals = _workspace_array(workspace, 'channel_totals', (1, width, 3), 'int32')
    cv2.reduce(frame[y0:y1, x0:x1], 0, cv2.REDUCE_SUM,
               dst=channel_totals, dtype=cv2.CV_32S)
    METRICS.add('channel_reduce_ms', (time.monotonic() - started) * 1000)
    totals = _workspace_array(workspace, 'totals', (1, x1 - x0), 'int32')
    totals.fill(0)
    ranges = channel_ranges or {}
    for name, index in (('Blue', 0), ('Green', 1), ('Red', 2)):
        low, high = ranges.get(name, (x0, x1 - 1))
        left, right = max(x0, low) - x0, min(x1 - 1, high) - x0 + 1
        if left < right:
            totals[0, left:right] += channel_totals[0, left:right, index]
    return SpectrumFrame(bar, totals.reshape(-1).copy() if workspace is not None else totals.reshape(-1),
                         tuple(roi), maximum=(y1 - y0) * 255 * 3)


class CameraStream:
    """Publish the newest bar and spectrum; acquisition never waits for the LCD.

    Picamera2 owns the libcamera event thread. Its callback maps the full-size
    buffer without copying it; OpenCV processes only the ROI. The UI polls a
    single bar/spectrum pair, avoiding a queue of stale 12-megapixel frames.
    """

    def __init__(self, settings=None, capture_directory=None):
        self.settings = settings or CameraSettings()
        self._lock = threading.Lock()
        self._latest = None
        self._sensor_preview = None
        self._sensor_preview_enabled = False
        self._error = None
        self._camera = None
        self._started = False
        self._logged_frame = False
        self._last_callback = None
        self._processing_workspace = {}
        self._averager = FrameAverager(self.settings.frame_averaging)
        self._frame_duration_us = None
        self._exposure_us = self.settings.exposure_us
        self._capture_directory = Path.home() if capture_directory is None else Path(capture_directory)
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spectrum-save")
        self._lifecycle = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-lifecycle")
        self._capture_pending = False
        self._capture_busy = False
        self._api_capture = None
        self._capture_draft = None
        self._calibration = {'scale': {}, 'sensor_area': self.settings.roi,
                             'channel_ranges': {}}

    def __enter__(self):
        from picamera2 import Picamera2
        import cv2

        cv2.setNumThreads(1)

        self._camera = Picamera2()
        try:
            self._configure_and_start()
        except BaseException:
            self.close()
            raise
        return self

    def _configure_and_start(self):
        started = time.monotonic()
        config = self._camera.create_video_configuration(
            main={"size": self.settings.resolution, "format": "RGB888"},  # BGR in memory
            raw={"size": self.settings.resolution, "format": "SRGGB12_CSI2P"},
            sensor={"output_size": self.settings.resolution, "bit_depth": 12},
            buffer_count=2, queue=False)
        self._camera.configure(config)
        # Query timing only after configuring the exact mode; sensor_modes
        # probes/reconfigures every mode and is expensive on the Pi Zero.
        minimum, maximum, _ = self._camera.camera_controls["FrameDurationLimits"]
        requested = math.ceil(1_000_000 / self.settings.frame_rate)
        duration = max(minimum, requested, self.settings.exposure_us or 0)
        if requested < minimum:
            LOGGER.warning("Requested %.2f fps; configured sensor mode permits %.2f fps",
                           self.settings.frame_rate, 1_000_000 / minimum)
        controls = {"FrameDurationLimits": (duration, duration),
                    "AeEnable": self.settings.exposure_us is None}
        if self.settings.exposure_us is not None:
            controls["ExposureTime"] = self.settings.exposure_us
        for name, value in (("FrameDurationLimits", duration),
                            ("ExposureTime", self.settings.exposure_us)):
            if value is not None:
                low, high, _ = self._camera.camera_controls[name]
                if not low <= value <= high:
                    raise ValueError("%s must be between %s and %s us" % (name, low, high))
        actual = self._camera.camera_configuration()
        raw = actual.get("raw") or {}
        raw_size = tuple(raw.get("size", ()))
        raw_format = str(raw.get("format", ""))
        if raw_size != tuple(self.settings.resolution) or raw_format not in PACKED_12_FORMATS:
            raise RuntimeError(
                f"Camera did not configure packed {self.settings.resolution[0]}:{self.settings.resolution[1]}:12:P mode; "
                "actual raw size=%r format=%r" % (raw_size, raw_format))
        LOGGER.info("Configured raw stream: size=%s format=%s", raw_size, raw_format)
        self._raw_config = dict(raw)
        self._camera.set_controls(controls)
        self._frame_duration_us = duration
        self._camera.post_callback = self._on_frame
        self._camera.start(show_preview=False)
        with self._lock:
            self._started = True
            self._error = None
        LOGGER.info("Camera startup completed in %.2f s", time.monotonic() - started)
        LOGGER.info("Camera mode %s:%s:12:P; target %.2f fps; exposure %s",
                    *self.settings.resolution,
                    1_000_000 / duration, self.settings.exposure_us or "automatic")

    def _on_frame(self, request):
        from picamera2 import MappedArray

        started = time.monotonic()
        previous_callback, self._last_callback = self._last_callback, started
        if previous_callback is not None:
            METRICS.add('callback_interval_ms', (started - previous_callback) * 1000)
        timestamp = datetime.now().astimezone()
        with self._lock:
            capture = self._capture_pending
            self._capture_pending = False
        try:
            with self._lock:
                channel_ranges = deepcopy(self._calibration['channel_ranges'])
            with MappedArray(request, "main", write=False) as mapped:
                if self._sensor_preview_enabled:
                    import cv2
                    width, height = self.settings.resolution
                    factor = min(480 / width, 256 / height)
                    size = (round(width * factor), round(height * factor))
                    small = cv2.resize(mapped.array, size, interpolation=cv2.INTER_LINEAR)
                    pixels = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).tobytes()
                    with self._lock:
                        self._sensor_preview = (pixels, size, (width, height))
                    return
                current = process_frame(mapped.array, self.settings.roi, self.settings.resolution,
                                        self._processing_workspace, channel_ranges)
                x0, y0, x1, y1 = self.settings.roi
                frame = self._averager.add(current, mapped.array[y0:y1, x0:x1])
            if frame is None:
                if capture:
                    with self._lock:
                        self._capture_pending = True
                return
            metadata = request.get_metadata()
            if not self._logged_frame:
                LOGGER.info("Camera first frame: duration=%s us, exposure=%s us",
                            metadata.get("FrameDuration"), metadata.get("ExposureTime"))
                self._logged_frame = True
            with self._lock:
                overwritten = self._latest is not None
                self._latest = frame
                self._frame_duration_us = metadata.get("FrameDuration", self._frame_duration_us)
                self._exposure_us = metadata.get("ExposureTime", self._exposure_us)
            if capture:
                self._queue_capture(request, frame, timestamp, self._averager.image())
            METRICS.add('frames')
            frame_duration = metadata.get('FrameDuration')
            if isinstance(frame_duration, (int, float)) and frame_duration > 0:
                METRICS.add('sensor_frame_ms', frame_duration / 1000)
            if overwritten:
                METRICS.add('ui_overwrites')
            METRICS.add('processing_ms', (time.monotonic() - started) * 1000)
        except Exception as exc:
            # Surface callback failures to the UI instead of killing libcamera's
            # event thread and leaving an apparently healthy, frozen preview.
            with self._lock:
                self._error = exc
                if capture:
                    self._capture_busy = False
                    self._fail_api_capture(exc)

    def request_capture(self):
        """Reserve a naming draft; allow only one capture in flight on the Pi Zero."""
        with self._lock:
            if not self._started or self._capture_busy or self._sensor_preview_enabled:
                LOGGER.info("Capture unavailable: camera stopped or a capture is still saving")
                return False
            self._capture_pending = self._capture_busy = True
        LOGGER.info("Capture requested")
        return True

    def request_api_capture(self, name):
        """Reserve the next frame and complete only after its file is saved."""
        with self._lock:
            if not self._started or self._capture_busy or self._sensor_preview_enabled:
                return None
            future = Future()
            self._api_capture = (name, future)
            self._capture_pending = self._capture_busy = True
            return future

    def _fail_api_capture(self, error):
        # Called with the camera lock held.
        if self._api_capture is not None:
            _, future = self._api_capture
            self._api_capture = None
            future.set_exception(error)

    def _save_api_capture(self, record, future):
        from .capture import save_capture
        try:
            path = save_capture(record, self._capture_directory)
            future.set_result((path, record))
        except Exception as exc:
            future.set_exception(exc)
        finally:
            with self._lock:
                self._api_capture = None
                self._capture_busy = False

    def settings_snapshot(self):
        with self._lock:
            return {
                "frame_rate": (1_000_000 / self._frame_duration_us
                               if self._frame_duration_us else self.settings.frame_rate),
                "resolution": self.settings.resolution,
                "exposure_us": self._exposure_us,
                "frame_averaging": self.settings.frame_averaging,
            }

    def _queue_capture(self, request, frame, timestamp, averaged_image):
        try:
            import numpy as np
            expected = (frame.roi[3] - frame.roi[1], frame.roi[2] - frame.roi[0], 3)
            if (not isinstance(averaged_image, np.ndarray)
                    or averaged_image.dtype != np.uint8 or averaged_image.shape != expected):
                raise ValueError('Averaged camera image does not match the spectrum ROI')
            with self._lock:
                calibration = deepcopy(self._calibration)
            calibration['sensor_area'] = tuple(frame.roi)
            metadata = request.get_metadata()
            record = {
                "timestamp": timestamp,
                "instrument_settings": {
                    "exposure_time_us": metadata["ExposureTime"],
                    "raw_camera_format": self._raw_config.copy(),
                    "averaged_camera_format": {
                        "size": (frame.roi[2] - frame.roi[0], frame.roi[3] - frame.roi[1]),
                        "format": "BGR888", "origin": frame.roi[:2],
                        "sensor_size": self.settings.resolution,
                    },
                    "frame_averaging": self.settings.frame_averaging,
                    "calibration_settings": calibration,
                    "intensity_calculation": "rgb_channel_sum",
                },
                "averaged_camera_output": averaged_image,
                "spectrum_intensity": frame.intensity.copy(),
                "spectrum_maximum": frame.maximum,
                "spectrum_bar": frame.bar,
                "spectrum_roi": frame.roi,
                "peak_labels": {},
            }
            with self._lock:
                api = self._api_capture
            if api is not None:
                record['name'] = api[0]
                self._writer.submit(self._save_api_capture, record, api[1])
            else:
                with self._lock:
                    self._capture_draft = record
        except Exception as exc:
            LOGGER.exception("Could not prepare spectrum capture")
            with self._lock:
                self._capture_busy = False
                self._fail_api_capture(exc)

    def take_capture_draft(self):
        with self._lock:
            record, self._capture_draft = self._capture_draft, None
            return record

    def discard_capture(self):
        # Caller pauses acquisition first, waiting for any callback to finish.
        with self._lock:
            self._capture_draft = None
            self._capture_pending = self._capture_busy = False

    def save_named_capture(self, record, name):
        from .capture import save_capture
        named = dict(record, name=name)
        return self._writer.submit(save_capture, named, self._capture_directory)

    def poll(self):
        """Return the newest bar and spectrum together, once per frame."""
        with self._lock:
            if self._error is not None:
                raise RuntimeError("Camera frame processing failed") from self._error
            result, self._latest = self._latest, None
            return result

    def close(self):
        try:
            # Apply queued pause/resume requests before taking ownership of the
            # camera for final shutdown.
            self._lifecycle.shutdown(wait=True)
            self._close_camera()
        finally:
            # Camera callbacks have finished before waiting for the disk writer.
            self._writer.shutdown(wait=True)
            with self._lock:
                self._fail_api_capture(RuntimeError("Camera closed"))
                self._capture_pending = self._capture_busy = False

    def pause(self):
        """Stop acquisition; allow an already queued disk write to finish."""
        started = time.monotonic()
        with self._lock:
            if not self._started:
                return
            self._started = False
            if self._capture_pending:
                self._fail_api_capture(RuntimeError('Capture cancelled: camera paused'))
                self._capture_pending = self._capture_busy = False
                LOGGER.info("Pending capture cancelled while pausing")
        # Do not hold the lock while stop waits for camera callbacks.
        self._camera.stop()
        with self._lock:
            self._latest = None
        METRICS.add('stop_ms', (time.monotonic() - started) * 1000)
        LOGGER.info("Camera paused")

    def request_pause(self):
        """Queue a camera stop so a touchscreen handler never blocks on it."""
        return self._lifecycle.submit(self._lifecycle_call, self.pause)

    def _lifecycle_call(self, action):
        try:
            action()
        except Exception as exc:
            with self._lock:
                self._error = exc
            LOGGER.exception('Camera lifecycle transition failed')

    def set_calibration(self, calibration):
        """Own a copy so later UI edits cannot change a queued capture."""
        with self._lock:
            self._calibration = deepcopy(calibration)

    def set_roi(self, roi):
        """Apply an accepted sensor area while acquisition is paused."""
        self.settings = replace(self.settings, roi=tuple(roi))
        self._averager.reset()

    def resume(self):
        """Restart the existing configuration without probing or reallocating."""
        with self._lock:
            camera = self._camera
            stopped = not self._started
        if camera is not None and stopped:
            started = time.monotonic()
            self._averager.reset()
            camera.start(show_preview=False)
            with self._lock:
                self._started = True
                self._error = None
            METRICS.add('start_ms', (time.monotonic() - started) * 1000)
            LOGGER.info("Camera resumed")

    def request_resume(self, capture=False):
        """Queue a restart, optionally reacquiring a cancelled naming draft."""
        def restart():
            self.resume()
            if capture:
                self.request_capture()
        return self._lifecycle.submit(self._lifecycle_call, restart)

    def reconfigure(self, settings):
        """Apply a complete configuration while acquisition is paused."""
        if self._camera is None:
            self.settings = settings
            self._averager = FrameAverager(settings.frame_averaging)
            return
        self.pause()
        self.settings = settings
        self._averager = FrameAverager(settings.frame_averaging)
        self._processing_workspace.clear()
        self._logged_frame = False
        self._last_callback = None
        self._exposure_us = settings.exposure_us
        self._configure_and_start()

    def request_reconfigure(self, settings):
        """Queue camera allocation and restart away from the UI thread."""
        return self._lifecycle.submit(self._lifecycle_call,
                                      lambda: self.reconfigure(settings))

    def request_sensor_preview(self, settings):
        def start():
            self.pause()
            self._sensor_preview_enabled = True
            with self._lock:
                self._sensor_preview = None
            if settings != self.settings:
                self.reconfigure(settings)
            else:
                self.resume()
        return self._lifecycle.submit(self._lifecycle_call, start)

    def poll_sensor_preview(self):
        with self._lock:
            if self._error is not None:
                raise RuntimeError("Camera preview failed") from self._error
            preview, self._sensor_preview = self._sensor_preview, None
            return preview

    def request_end_sensor_preview(self, roi):
        def finish():
            self.pause()
            self._sensor_preview_enabled = False
            with self._lock:
                self._sensor_preview = None
            self.set_roi(roi)
        return self._lifecycle.submit(self._lifecycle_call, finish)

    def request_cancel_capture(self):
        """Cancel naming safely after any active camera callback, then restart."""
        def cancel():
            self.pause()
            self.discard_capture()
            self.resume()
        return self._lifecycle.submit(self._lifecycle_call, cancel)

    def _close_camera(self):
        if self._camera is not None:
            try:
                if self._started:
                    self._camera.stop()
            finally:
                self._camera.close()
                self._camera = None
                self._started = False

    def __exit__(self, *exc):
        self.close()
