"""Full-resolution IMX477 acquisition with an OpenCV spectrum-bar preview."""

from dataclasses import dataclass, replace, field
from copy import deepcopy
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from pathlib import Path


LOGGER = logging.getLogger(__name__)
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

    def __post_init__(self):
        if not math.isfinite(self.frame_rate) or self.frame_rate <= 0:
            raise ValueError("Frame rate must be positive and finite")
        if self.exposure_us is not None and self.exposure_us <= 0:
            raise ValueError("Exposure must be a positive number of microseconds")


def spectrum_bar(frame, roi=SPECTRUM_ROI, resolution=SENSOR_SIZE):
    """Crop full-resolution BGR pixels before resizing and converting to RGB."""
    import cv2

    if frame.shape != (resolution[1], resolution[0], 3):
        raise ValueError(f"Expected a {resolution[0]}x{resolution[1]} three-channel camera frame")
    x0, y0, x1, y1 = roi
    crop = frame[y0:y1, x0:x1]
    preview = cv2.resize(crop, BAR_SIZE, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(preview, cv2.COLOR_BGR2RGB).tobytes()


@dataclass(frozen=True)
class SpectrumFrame:
    bar: bytes
    intensity: object  # Owned int32 array, one total per sensor column.
    roi: tuple = SPECTRUM_ROI
    calibration: dict = field(default_factory=dict)


def process_frame(frame, roi=SPECTRUM_ROI, resolution=SENSOR_SIZE):
    """Sum grayscale intensity vertically in the original, unscaled ROI."""
    import cv2

    bar = spectrum_bar(frame, roi, resolution)
    x0, y0, x1, y1 = roi
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    totals = cv2.reduce(gray, 0, cv2.REDUCE_SUM, dtype=cv2.CV_32S).reshape(-1)
    return SpectrumFrame(bar, totals, tuple(roi))


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
        self._error = None
        self._camera = None
        self._started = False
        self._logged_frame = False
        self._frame_duration_us = None
        self._exposure_us = self.settings.exposure_us
        self._capture_directory = Path.home() if capture_directory is None else Path(capture_directory)
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spectrum-save")
        self._capture_pending = False
        self._capture_busy = False
        self._api_capture = None
        self._capture_draft = None
        self._defer_save = False
        self._calibration = {'scale': {}, 'sensor_area': self.settings.roi}

    def __enter__(self):
        started = time.monotonic()
        from picamera2 import Picamera2
        import cv2

        cv2.setNumThreads(1)

        self._camera = Picamera2()
        try:
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
            # Reject unsupported timing rather than silently letting libcamera
            # clamp a user-specified exposure or very low frame rate.
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
            # libcamera may adjust Bayer order. That does not change resolution,
            # bit depth, or packing, and the ISP handles colour conversion for
            # the processed main stream used by our preview.
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
            self._started = True
            LOGGER.info("Camera startup completed in %.2f s", time.monotonic() - started)
            LOGGER.info("Camera mode %s:%s:12:P; target %.2f fps; exposure %s",
                        *self.settings.resolution,
                        1_000_000 / duration, self.settings.exposure_us or "automatic")
        except BaseException:
            self.close()
            raise
        return self

    def _on_frame(self, request):
        from picamera2 import MappedArray

        started = time.monotonic()
        timestamp = datetime.now().astimezone()
        with self._lock:
            capture = self._capture_pending
            self._capture_pending = False
        try:
            with MappedArray(request, "main", write=False) as mapped:
                frame = process_frame(mapped.array, self.settings.roi, self.settings.resolution)
            metadata = request.get_metadata()
            if not self._logged_frame:
                LOGGER.info("Camera first frame: duration=%s us, exposure=%s us",
                            metadata.get("FrameDuration"), metadata.get("ExposureTime"))
                self._logged_frame = True
            with self._lock:
                self._latest = frame
                self._frame_duration_us = metadata.get("FrameDuration", self._frame_duration_us)
                self._exposure_us = metadata.get("ExposureTime", self._exposure_us)
            if capture:
                self._queue_capture(request, frame, timestamp)
            LOGGER.debug("Camera ROI processing %.1f ms", (time.monotonic() - started) * 1000)
        except Exception as exc:
            # Surface callback failures to the UI instead of killing libcamera's
            # event thread and leaving an apparently healthy, frozen preview.
            with self._lock:
                self._error = exc
                if capture:
                    self._capture_busy = False
                    self._fail_api_capture(exc)

    def request_capture(self, defer_save=False):
        """Save the next frame; allow only one capture in flight on the Pi Zero."""
        with self._lock:
            if not self._started or self._capture_busy:
                LOGGER.info("Capture unavailable: camera stopped or a capture is still saving")
                return False
            self._defer_save = defer_save
            self._capture_pending = self._capture_busy = True
        LOGGER.info("Capture requested")
        return True

    def request_api_capture(self, name):
        """Reserve the next frame and complete only after its file is saved."""
        with self._lock:
            if not self._started or self._capture_busy:
                return None
            future = Future()
            self._api_capture = (name, future)
            self._defer_save = False
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
            }

    def _queue_capture(self, request, frame, timestamp):
        try:
            with self._lock:
                calibration = deepcopy(self._calibration)
            calibration['sensor_area'] = tuple(frame.roi)
            metadata = request.get_metadata()
            record = {
                "timestamp": timestamp,
                "instrument_settings": {
                    "exposure_time_us": metadata["ExposureTime"],
                    "raw_camera_format": self._raw_config.copy(),
                    "calibration_settings": calibration,
                },
                # make_array copies the packed sensor buffer before libcamera
                # recycles it. Never retain the mapped camera buffer in a worker.
                "raw_camera_output": request.make_array("raw"),
                "spectrum_intensity": frame.intensity.copy(),
                "spectrum_bar": frame.bar,
                "spectrum_roi": frame.roi,
            }
            with self._lock:
                api = self._api_capture
            if api is not None:
                record['name'] = api[0]
                self._writer.submit(self._save_api_capture, record, api[1])
            elif self._defer_save:
                with self._lock:
                    self._capture_draft = record
            else:
                self._writer.submit(self._save_capture, record)
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

    def _save_capture(self, record):
        from .capture import save_capture

        try:
            path = save_capture(record, self._capture_directory)
            LOGGER.info("Saved spectrum to %s", path)
        except Exception:
            LOGGER.exception("Could not save spectrum capture")
        finally:
            with self._lock:
                self._capture_busy = False

    def poll(self):
        """Return the newest bar and spectrum together, once per frame."""
        with self._lock:
            if self._error is not None:
                raise RuntimeError("Camera frame processing failed") from self._error
            result, self._latest = self._latest, None
            return result

    def close(self):
        try:
            self._close_camera()
        finally:
            # Camera callbacks have finished before waiting for the disk writer.
            self._writer.shutdown(wait=True)
            with self._lock:
                self._fail_api_capture(RuntimeError("Camera closed"))
                self._capture_pending = self._capture_busy = False

    def pause(self):
        """Stop acquisition; allow an already queued disk write to finish."""
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
        LOGGER.info("Camera paused")

    def set_calibration(self, calibration):
        """Own a copy so later UI edits cannot change a queued capture."""
        with self._lock:
            self._calibration = deepcopy(calibration)

    def set_roi(self, roi):
        """Apply an accepted sensor area while acquisition is paused."""
        self.settings = replace(self.settings, roi=tuple(roi))

    def resume(self):
        """Restart the existing configuration without probing or reallocating."""
        if self._camera is not None and not self._started:
            self._camera.start(show_preview=False)
            self._started = True
            LOGGER.info("Camera resumed")

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
