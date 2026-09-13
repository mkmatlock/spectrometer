import unittest
from unittest.mock import MagicMock, Mock, patch

from spectrometer.camera import CameraStream
from spectrometer.hardware import LCDBackend
from spectrometer.ui import SpectrometerUI


class PowerTests(unittest.TestCase):
    def test_press_hold_release_produces_one_toggle(self):
        backend = LCDBackend()
        backend.power_button = Mock()
        events = []
        for down in (False, True, True, True, False, True):
            backend.power_button.is_pressed = down
            events.append(backend.power_pressed())
        self.assertEqual(events, [False, True, False, False, False, True])

    def test_pause_resume_preserves_camera_and_cancels_pending_capture(self):
        stream = CameraStream()
        camera = stream._camera = Mock()
        stream._started = True
        try:
            stream.request_capture()
            stream.pause()
            self.assertFalse(stream.request_capture())
            self.assertFalse(stream._capture_pending)
            self.assertFalse(stream._capture_busy)
            self.assertIsNone(stream.poll())
            camera.stop.assert_called_once_with()
            camera.close.assert_not_called()
            stream.resume()
            camera.start.assert_called_once_with(show_preview=False)
            self.assertTrue(stream._started)
            camera.configure.assert_not_called()
        finally:
            stream.close()

    def test_paused_ui_skips_touch_camera_and_drawing_until_wake(self):
        camera = MagicMock()
        camera.poll.return_value = None
        hardware = Mock()
        hardware.power_pressed.side_effect = [True, False, True]
        hardware.read_touch.return_value = None
        stopping = Mock()
        stopping.is_set.side_effect = [False, False, False, True]
        ui = SpectrometerUI(camera=camera)
        with patch("spectrometer.hardware.LCDBackend") as factory, \
                patch("spectrometer.ui.threading.Event", return_value=stopping):
            factory.return_value.__enter__.return_value = hardware
            ui.run()
        camera.pause.assert_called_once_with()
        camera.resume.assert_called_once_with()
        camera.poll.assert_called_once_with()
        hardware.read_touch.assert_called_once_with()
        self.assertEqual(hardware.present.call_count, 2)  # Initial draw and wake.
        self.assertEqual([c.args[0] for c in hardware.set_screen_active.call_args_list],
                         [False, True])

    def test_backlight_toggle(self):
        backend = LCDBackend()
        backend.display = Mock()
        backend.set_screen_active(False)
        backend.set_screen_active(True)
        self.assertEqual([c.args[0] for c in backend.display.bl_DutyCycle.call_args_list],
                         [0, 100])
