from datetime import datetime
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
import pygame

from spectrometer.camera import CameraStream, SpectrumFrame
from spectrometer.ui import SpectrometerUI


class NamedCaptureTests(unittest.TestCase):
    def test_accept_saves_named_frozen_frame_and_cancel_saves_nothing(self):
        for accept in (True, False):
            with self.subTest(accept=accept), tempfile.TemporaryDirectory() as directory:
                camera = CameraStream(capture_directory=directory)
                camera._camera = Mock()
                camera._started = True
                camera._raw_config = {}
                ui = SpectrometerUI(camera=camera)
                try:
                    ui._name_capture()
                    self.assertEqual(ui.mode, 'capture')
                    self.assertFalse(ui._poll_camera())
                    ui._capture_key('Shift')
                    ui._capture_key('T')
                    ui._capture_key('e')
                    ui._capture_key('s')
                    ui._capture_key('t')
                    ui._capture_key('Space')
                    ui._capture_key('2')
                    ui._capture_key('Bksp')
                    ui._capture_key('1')
                    request = Mock()
                    request.get_metadata.return_value = {'ExposureTime': 100}
                    request.make_array.return_value = np.array([1, 2], np.uint8)
                    frame = SpectrumFrame(b'', np.array([123, 456], np.int32))
                    camera._queue_capture(request, frame, datetime(2026, 9, 14, 12, 0, 0))
                    frame.intensity[:] = 0
                    ui._poll_capture()
                    camera._camera.stop.assert_called_once()
                    self.assertEqual(list(Path(directory).glob('*.pkl')), [])
                    pygame.font.init()
                    ui.draw(pygame.Surface((480, 320)), pygame.font.Font(None, 22))
                    if accept:
                        ui._accept_capture()
                        ui._capture_save.result(timeout=5)
                        ui._poll_capture()
                        record = pickle.loads(next(Path(directory).glob('*.pkl')).read_bytes())
                        self.assertEqual(record['name'], 'Test 1')
                        np.testing.assert_array_equal(record['spectrum_intensity'], [123, 456])
                    else:
                        ui._cancel_capture()
                        self.assertEqual(list(Path(directory).glob('*.pkl')), [])
                    camera._lifecycle.submit(lambda: None).result(timeout=1)
                    self.assertEqual(ui.mode, 'live')
                    self.assertFalse(camera._capture_busy)
                finally:
                    camera.close()
                    ui.review.close()
                    ui.settings_view.close()
                    pygame.font.quit()
