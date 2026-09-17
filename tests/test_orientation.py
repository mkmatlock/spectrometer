import unittest
from unittest.mock import Mock

import numpy as np

from spectrometer.camera import BAR_SIZE, SpectrumFrame
from spectrometer.ui import SpectrometerUI


class OrientationTests(unittest.TestCase):
    def test_blue_left_red_right_and_sensor_peak_coordinates_preserved(self):
        rgb = np.zeros((BAR_SIZE[1], BAR_SIZE[0], 3), np.uint8)
        rgb[:, :231, 0] = 255
        rgb[:, 231:, 2] = 255
        intensity = np.zeros(3500, np.int32)
        intensity[100] = 191250
        original = intensity.copy()
        frame = SpectrumFrame(rgb.tobytes(), intensity)
        camera = Mock()
        camera.poll.return_value = frame
        ui = SpectrometerUI(camera=camera)
        try:
            ui._poll_camera()
            self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (0, 0, 255))
            self.assertEqual(ui._camera_bar.get_at((461, 0))[:3], (255, 0, 0))
            ui._reset_peaks(intensity)
            position = ui._peaks.positions[0]
            self.assertGreater(position[0], 450)
            self.assertEqual(ui._peaks.select(position), 100)
            peak_points = [p for p in ui._plot.points if p[1] == ui._plot.AREA.top]
            self.assertTrue(all(p[0] > 440 for p in peak_points))
            ui._saved_buttons = []
            ui._apply_filter(frame)
            self.assertEqual(ui._camera_bar.get_at((0, 0))[:3], (0, 0, 255))
            np.testing.assert_array_equal(intensity, original)
            self.assertEqual(frame.bar, rgb.tobytes())
        finally:
            ui.review.close()
            ui.settings_view.close()
