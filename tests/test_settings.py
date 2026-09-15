import unittest
from unittest.mock import Mock, patch

from spectrometer.camera import CameraStream
from spectrometer.settings import SettingsView, network_status
from spectrometer.ui import SpectrometerUI


class SettingsTests(unittest.TestCase):
    def test_network_values_and_missing_tools(self):
        with patch('spectrometer.settings._output', side_effect=['192.168.1.42 fe80::1', 'Lab WiFi']):
            self.assertEqual(network_status(), ('192.168.1.42', 'Lab WiFi'))
        with patch('spectrometer.settings._output', side_effect=['192.168.1.42', None, '*:Lab:WiFi']):
            self.assertEqual(network_status(), ('192.168.1.42', 'Lab:WiFi'))
        with patch('spectrometer.settings._output', return_value=None):
            self.assertEqual(network_status(), ('Unavailable', 'Not connected / unavailable'))

    def test_read_only_settings_navigation_and_background_network(self):
        camera = Mock()
        camera.settings_snapshot.return_value = {'frame_rate': 5.0, 'resolution': (4056, 3040),
                                                 'exposure_us': 20000}
        ui = SpectrometerUI(camera=camera)
        try:
            with patch('spectrometer.settings.network_status', return_value=('10.0.0.2', 'Lab')):
                ui._open_settings()
                ui.settings_view.future.result(timeout=5)
                ui._poll_settings()
            self.assertEqual(ui.mode, 'settings')
            self.assertEqual(ui.settings_view.rows, [('Frame rate', '5 fps'),
                             ('Camera resolution', '4056 x 3040'), ('Exposure time', '20 ms'),
                             ('IP address', '10.0.0.2'), ('Wi-Fi network', 'Lab')])
            self.assertFalse(ui._poll_camera())
            ui._pointer_event('lcd', (40, 50), True)
            ui._pointer_event('lcd', (40, 50), False)
            self.assertEqual(ui.mode, 'settings')
            ui.buttons[0][2]()
            self.assertTrue(ui._calibration_dialog)
            ui.buttons[1][2]()
            self.assertEqual(ui.mode, 'settings')
            ui.buttons[1][2]()
            self.assertEqual(ui.mode, 'live')
            camera.request_pause.assert_called_once_with()
            camera.request_resume.assert_called_once_with()
        finally:
            ui.review.close()
            ui.settings_view.close()

    def test_snapshot_uses_actual_frame_timing_and_exposure(self):
        camera = CameraStream()
        try:
            camera._frame_duration_us = 250000
            camera._exposure_us = 12345
            self.assertEqual(camera.settings_snapshot(), {'frame_rate': 4.0,
                             'resolution': (4056, 3040), 'exposure_us': 12345})
        finally:
            camera.close()

    def test_calibration_dialog_selection_and_modal_input(self):
        ui = SpectrometerUI()
        try:
            with patch('spectrometer.settings.network_status', return_value=('Unavailable', 'Unavailable')):
                ui._open_settings()
                ui.settings_view.future.result(timeout=5)
            ui._calibrate()
            self.assertEqual([b[0] for b in ui.buttons], ['Select', 'Back'])
            self.assertEqual([r[0] for r in ui._calibration_rows], ['Background', 'Sensor', 'Scale'])
            ui._select_calibration()
            self.assertIn('Choose', ui._calibration_message)
            for index, (name, rect) in enumerate(ui._calibration_rows[:1]):
                ui._pointer_event('lcd', rect.center, True)
                ui._pointer_event('lcd', rect.center, False)
                self.assertEqual(ui._calibration_selected, index)
                ui._select_calibration()
                self.assertEqual(ui._calibration_message, name + ': not implemented yet')
            ui._pointer_event('lcd', (350, 300), True)
            ui._pointer_event('lcd', (350, 300), False)
            self.assertTrue(ui._calibration_dialog)  # Underlying Settings Back is blocked.
            ui._back_calibration()
            self.assertFalse(ui._calibration_dialog)
            self.assertEqual(ui.mode, 'settings')
            self.assertEqual([b[0] for b in ui.buttons], ['Calibrate', 'Back'])
        finally:
            ui.review.close()
            ui.settings_view.close()
