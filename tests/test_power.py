import unittest
from unittest.mock import MagicMock, Mock, patch

from spectrometer.camera import CameraStream
from spectrometer.hardware import LCDBackend
from spectrometer.ui import SpectrometerUI


class PowerTests(unittest.TestCase):
    def test_shutdown_yes_no_and_failure(self):
        import subprocess
        ui = SpectrometerUI(camera=Mock())
        self.addCleanup(ui.review.close)
        self.addCleanup(ui.settings_view.close)
        buttons = ui.buttons
        with patch('subprocess.run') as run:
            ui._ask_shutdown()
            self.assertEqual(ui.mode, 'shutdown')
            self.assertEqual([b[0] for b in ui.buttons], ['Yes', 'No'])
            run.assert_not_called()
            ui._pointer_event('lcd', (330, 190), True)
            ui._pointer_event('lcd', (330, 190), False)
            self.assertEqual(ui.mode, 'live')
            self.assertIs(ui.buttons, buttons)
            run.assert_not_called()
            ui._ask_shutdown()
            run.side_effect = subprocess.CalledProcessError(1, 'shutdown')
            with self.assertLogs('spectrometer.ui', level='ERROR'):
                ui._confirm_shutdown()
            self.assertEqual(len(ui.buttons), 2)
            self.assertIn('failed', ui._shutdown_message)
            run.side_effect = None
            ui._pointer_event('lcd', (140, 190), True)
            ui._pointer_event('lcd', (140, 190), False)
            run.assert_called_with(['sudo', '-n', '/sbin/shutdown', 'now'],
                                   check=True, capture_output=True, timeout=5)
            self.assertEqual(ui.buttons, [])

    def test_hold_wakes_paused_screen_and_opens_confirmation_without_shutdown(self):
        camera = MagicMock()
        camera.poll.return_value = None
        hardware = Mock()
        hardware.power_event.side_effect = ['short', 'hold', None]
        hardware.read_touch.return_value = None
        stopping = Mock()
        stopping.is_set.side_effect = [False, False, False, True]
        ui = SpectrometerUI(camera=camera)
        with patch('spectrometer.hardware.LCDBackend') as factory, \
                patch('spectrometer.ui.threading.Event', return_value=stopping), \
                patch('subprocess.run') as run:
            factory.return_value.__enter__.return_value = hardware
            ui.run()
            run.assert_not_called()
        self.assertEqual(ui.mode, 'shutdown')
        camera.resume.assert_not_called()
        self.assertEqual([c.args[0] for c in hardware.set_screen_active.call_args_list], [False, True])

    def test_short_release_hold_threshold_and_no_repeat(self):
        backend = LCDBackend()
        backend.power_button = Mock()
        events = []
        samples = [(0, False), (1, True), (1.2, False), (2, True),
                   (4.99, True), (5, True), (6, True), (7, False),
                   (8, True), (8.2, False)]
        for now, down in samples:
            backend.power_button.is_pressed = down
            with patch('spectrometer.hardware.time.monotonic', return_value=now):
                events.append(backend.power_event())
        self.assertEqual(events, [None, None, 'short', None, None, 'hold',
                                  None, None, None, 'short'])

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
        hardware.power_event.side_effect = ['short', None, 'short']
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
