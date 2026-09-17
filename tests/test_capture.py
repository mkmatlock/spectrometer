from datetime import datetime, timezone
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from spectrometer.camera import CameraStream, SpectrumFrame
from spectrometer.capture import save_capture
from spectrometer.ui import SpectrometerUI
from tests.spectrum_fixtures import spectrum_record


class CaptureTests(unittest.TestCase):
    def test_round_trip_and_same_second_does_not_overwrite(self):
        record = spectrum_record(timestamp=datetime(2026, 9, 12, 21, 38, 53,
                                                     tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as directory:
            path = save_capture(record, directory)
            self.assertEqual(path.name, "spectrum-2026-09-12-21-38-53.pkl")
            with path.open("rb") as source:
                restored = pickle.load(source)
            self.assertEqual(restored["timestamp"], record["timestamp"])
            self.assertEqual(restored["instrument_settings"], record["instrument_settings"])
            np.testing.assert_array_equal(restored["averaged_camera_output"], record["averaged_camera_output"])
            np.testing.assert_array_equal(restored["spectrum_intensity"], record["spectrum_intensity"])
            with self.assertRaises(FileExistsError):
                save_capture(record, directory)
            self.assertEqual(list(Path(directory).glob('spectrum-*.pkl')), [path])

    def test_failed_write_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("spectrometer.capture.pickle.dump", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                save_capture(spectrum_record(timestamp=datetime.now()), directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_capture_uses_actual_exposure_and_owned_data(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = CameraStream(capture_directory=directory)
            calibration = {'scale': {100: 700, 200: 500},
                           'sensor_area': (0, 1550, 3500, 1800),
                           'channel_ranges': {},
                           'future_calibration': {'values': [1, 2]}}
            stream.set_calibration(calibration)
            stream._raw_config = {"size": (4056, 3040), "format": "SBGGR12_CSI2P", "stride": 6084}
            request = Mock()
            request.get_metadata.return_value = {"ExposureTime": 4321}
            averaged = np.full((2, 500, 3), (1, 2, 3), dtype=np.uint8)
            from spectrometer.camera import BAR_SIZE
            intensity = np.arange(500, dtype=np.int32)
            frame = SpectrumFrame(bytes(BAR_SIZE[0] * BAR_SIZE[1] * 3), intensity.copy(),
                                  (0, 0, 500, 2), maximum=2 * 255 * 3)
            timestamp = datetime(2026, 9, 12, 20, 10, 0, tzinfo=timezone.utc)
            stream._queue_capture(request, frame, timestamp, averaged)
            calibration['scale'][100] = 900
            calibration['future_calibration']['values'][0] = 9
            stream.set_calibration(calibration)
            frame.intensity[:] = 0
            draft = stream.take_capture_draft()
            self.assertEqual(list(Path(directory).glob('*.pkl')), [])
            stream.save_named_capture(draft, 'Named capture').result(timeout=5)
            stream.close()  # Must wait for the writer.
            path = next(Path(directory).glob("*.pkl"))
            with path.open("rb") as source:
                record = pickle.load(source)
            request.make_array.assert_not_called()
            self.assertEqual(record["instrument_settings"], {
                "exposure_time_us": 4321,
                "raw_camera_format": stream._raw_config,
                "averaged_camera_format": {"size": (500, 2), "format": "BGR888",
                                             "origin": (0, 0), "sensor_size": (4056, 3040)},
                "frame_averaging": 3,
                "calibration_settings": {
                    "scale": {100: 700, 200: 500}, "sensor_area": frame.roi,
                    "channel_ranges": {},
                    "future_calibration": {"values": [1, 2]}},
                "intensity_calculation": "rgb_channel_sum",
            })
            self.assertNotIn("raw_camera_format", record)
            self.assertNotIn('calibration_settings', record)
            np.testing.assert_array_equal(record['averaged_camera_output'], averaged)
            self.assertNotIn('raw_camera_output', record)
            self.assertEqual(record['instrument_settings']['calibration_settings'], {
                'scale': {100: 700, 200: 500}, 'sensor_area': frame.roi,
                'channel_ranges': {},
                'future_calibration': {'values': [1, 2]}})
            np.testing.assert_array_equal(record["spectrum_intensity"], intensity)
            self.assertEqual(record['spectrum_maximum'], 2 * 255 * 3)
            self.assertEqual(record['name'], 'Named capture')
            self.assertEqual(record["timestamp"], timestamp)

    def test_capture_button_requests_one_pending_frame(self):
        stream = CameraStream()
        try:
            self.assertFalse(stream.request_capture())
            stream._started = True
            ui = SpectrometerUI(camera=stream)
            ui._pointer_event("lcd", (80, 280), True)
            ui._pointer_event("lcd", (80, 280), False)
            self.assertTrue(stream._capture_pending)
            self.assertFalse(stream.request_capture())
        finally:
            stream.close()
