"""Software-rendered touchscreen UI with a live camera spectrum bar."""

import logging
import threading
import time
from copy import deepcopy
import pygame

from .performance import PerformanceMetrics


LOGGER = logging.getLogger(__name__)
METRICS = PerformanceMetrics('ui')
SCREEN_SIZE = (480, 320)
BACKGROUND = "#111820"
PANEL = "#1b2632"
BORDER = "#405367"
TEXT = "#edf3f8"
MUTED = "#a9bacb"
BUTTON = "#28465d"
PRESSED = "#3c718f"


class SpectrometerUI:
    """Own the layout and event loop, with optional callbacks for future actions.

    Callbacks run on the UI thread and should return promptly; camera acquisition
    and spectrum processing should eventually run outside this event loop.
    """

    def __init__(self, *, fullscreen=True, on_capture=None, on_review=None,
                 on_settings=None, camera=None, review_directory=None, calibration_settings=None,
                 on_calibration_changed=None, camera_settings=None,
                 on_camera_settings_changed=None):
        self.fullscreen = fullscreen
        self.camera = camera
        self._camera_bar = None
        self._fonts = {}
        from .plot import SpectrumPlot

        self._plot = SpectrumPlot()
        from .review import ReviewList

        self.review = ReviewList(review_directory)
        from .settings import SettingsView

        self.settings_view = SettingsView()
        self.mode = "live"
        self._redraw = False
        self._list_start = None
        self._delete_dialog = False
        self._filter_dialog = False
        self._scale_active = False
        self._scale_save_future = None
        self._scale_save_message = ''
        self._channel_active = False
        self._channel = None
        self._channels = {"Red", "Green", "Blue"}
        self._absorption = False
        self._blackbody = False
        self._blackbody_label = None
        self._mode_message = ''
        self._sensor_active = False
        self._sensor = None
        self._sensor_paused = False
        self._peak_dialog = False
        self._peak_touch = None
        self._peaks = None
        self._review_peak_label = None
        from .config import DEFAULTS
        self.calibration_settings = (calibration_settings if calibration_settings is not None
                                     else deepcopy(DEFAULTS['calibration']))
        if self.camera is not None:
            self.camera.set_calibration(self.calibration_settings)
        self._plot.set_calibration(self.calibration_settings['scale'])
        self._on_calibration_changed = on_calibration_changed
        if camera_settings is None and camera is not None:
            camera_settings = camera.settings_snapshot()
        self._camera_settings = deepcopy(DEFAULTS['camera'] if camera_settings is None
                                         else camera_settings)
        self._on_camera_settings_changed = on_camera_settings_changed
        self._settings_dirty = False
        self._settings_dialog = None
        self._settings_value = None
        self._settings_pressed = None
        self._slider_dragging = False
        self._resolution_selected = None
        self._keypad_open = False
        self._label_input = ""
        self._calibration_dialog = False
        self._calibration_selected = None
        self._calibration_pressed = None
        self._calibration_rows = [(name, pygame.Rect(64, 72 + i * 36, 352, 32))
                                  for i, name in enumerate(("Background", "Sensor", "Scale", "Channel"))]
        if on_capture is None and camera is not None:
            on_capture = self._name_capture
        self.spectrum_rect = pygame.Rect(8, 8, 464, 200)
        # Match the image pixels (inside its border) to the graph's data span.
        self.camera_slice_rect = pygame.Rect(
            self.spectrum_rect.x + self._plot.AREA.x - 1, 216,
            self._plot.AREA.width + 2, 40)
        self.buttons = [
            ("Capture", pygame.Rect(8, 264, 149, 48), on_capture),
            ("Review", pygame.Rect(165, 264, 150, 48), on_review or self._open_review),
            ("Settings", pygame.Rect(323, 264, 149, 48), on_settings or self._open_settings),
        ]
        self._capture_record = None
        self._capture_save = None
        self._pressed = None
        self._pointer = None
        self._live_buttons = self.buttons

    def _name_capture(self):
        if not self.camera.request_capture():
            return
        self.mode = 'capture'
        self._capture_record = None
        self._capture_name = ''
        self._capture_message = 'Capturing...'
        self._capture_upper = False
        self._capture_keyboard()
        self._redraw = True

    def _resume_pending_capture(self):
        if self.mode == 'capture' and self._capture_record is None:
            self.camera.request_resume(capture=True)

    def _capture_keyboard(self):
        self.buttons = []
        rows = ('1234567890', 'qwertyuiop', 'asdfghjkl⌫', '⇧zxcvbnm␣')
        actions = {'⇧': 'Shift', '␣': 'Space', '⌫': 'Bksp'}
        for row, keys in enumerate(rows):
            weights = [1.5 if key in ('⇧', '␣') else 1 for key in keys]
            unit = (464 - 4 * (len(keys) - 1)) / sum(weights)
            left = 8.0
            for key, weight in zip(keys, weights):
                width = round(left + unit * weight) - round(left)
                label = key.upper() if self._capture_upper else key
                action = actions.get(key, label)
                self.buttons.append((label, pygame.Rect(round(left), 66 + row * 48, width, 44),
                                     lambda value=action: self._capture_key(value)))
                left += unit * weight + 4
        self.buttons.extend([('Accept', pygame.Rect(8, 264, 228, 48),
                              self._accept_rename if self.mode == 'rename' else self._accept_capture),
                             ('Cancel', pygame.Rect(244, 264, 228, 48),
                              self._cancel_rename if self.mode == 'rename' else self._cancel_capture)])

    def _capture_key(self, key):
        if self.mode == "rename" and not self._rename_ready:
            return
        if key == 'Shift':
            self._capture_upper = not self._capture_upper
            self._capture_keyboard()
        elif key == 'Bksp':
            self._capture_name = self._capture_name[:-1]
        elif len(self._capture_name) < 64:
            self._capture_name += ' ' if key == 'Space' else key
        self._redraw = True

    def _accept_capture(self):
        if self._capture_record is None:
            return
        if not self._capture_name.strip():
            self._capture_message = 'Enter a name'
        else:
            self._capture_save = self.camera.save_named_capture(self._capture_record, self._capture_name.strip())
            self._capture_message = 'Saving...'
            self.buttons = []
        self._redraw = True

    def _cancel_capture(self):
        self.camera.request_cancel_capture()
        self._capture_record = None
        self.mode = 'live'
        self.buttons = self._live_buttons
        self._redraw = True

    def _poll_capture(self):
        if self.mode == 'rename':
            self._poll_rename()
            return
        if self.mode != 'capture':
            return
        if self._capture_save is not None:
            if not self._capture_save.done():
                return
            future, self._capture_save = self._capture_save, None
            try:
                future.result()
            except Exception:
                LOGGER.exception('Could not save named capture')
                self._capture_message = 'Save failed. Retry or cancel.'
                self._capture_keyboard()
                self._redraw = True
            else:
                self._cancel_capture()
            return
        if self._capture_record is None:
            record = self.camera.take_capture_draft()
            if record is not None:
                self.camera.request_pause()
                self._capture_record = record
                self._capture_message = 'Name spectrum'
                self._redraw = True

    def _open_settings(self):
        self.settings_view.open(self._camera_settings)
        self._settings_dirty = False
        self._settings_dialog = None
        self.mode = "settings"
        self.buttons = [("Calibrate", pygame.Rect(8, 264, 228, 48), self._calibrate),
                        ("Back", pygame.Rect(244, 264, 228, 48), self._exit_settings)]
        if self.camera is not None:
            self.camera.request_pause()
        self._redraw = True

    def _ask_shutdown(self):
        if self.mode == 'shutdown':
            return
        self._shutdown_view = (self.mode, self.buttons)
        self.mode = 'shutdown'
        self._shutdown_message = 'Shut down the Raspberry Pi?'
        self.buttons = [('Yes', pygame.Rect(64, 168, 172, 48), self._confirm_shutdown),
                        ('No', pygame.Rect(244, 168, 172, 48), self._cancel_shutdown)]
        if self.camera is not None:
            self.camera.request_pause()
        self._redraw = True

    def _cancel_shutdown(self):
        self.mode, self.buttons = self._shutdown_view
        self._resume_pending_capture()
        if self.camera is not None and (self.mode == 'live' or (self.mode == 'sensor' and not self._sensor_paused)):
            self.camera.request_resume()
        self._redraw = True

    def _confirm_shutdown(self):
        import subprocess
        try:
            subprocess.run(['sudo', '-n', '/sbin/shutdown', 'now'], check=True,
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            LOGGER.exception('Shutdown failed')
            self._shutdown_message = 'Shutdown failed. Retry or choose No.'
        else:
            self._shutdown_message = 'Shutting down...'
            self.buttons = []
        self._redraw = True

    def _calibrate(self):
        self._settings_buttons = self.buttons
        self._calibration_dialog = True
        self._calibration_selected = None
        self._calibration_message = "Calibration mode"
        self.buttons = [("Select", pygame.Rect(64, 224, 172, 48), self._select_calibration),
                        ("Back", pygame.Rect(244, 224, 172, 48), self._back_calibration)]
        self._redraw = True

    def _select_calibration(self):
        if self._calibration_selected is None:
            self._calibration_message = "Choose a calibration mode"
        else:
            name = self._calibration_rows[self._calibration_selected][0]
            if name in ("Scale", "Sensor", "Channel"):
                self._calibration_dialog = False
                self._scale_active = name == "Scale"
                self._sensor_active = name == "Sensor"
                self._channel_active = name == "Channel"
                self._live_view = (self._plot, self._camera_bar)
                if name == "Sensor":
                    self._open_sensor()
                    return
                self.review.refresh()
                self._review_list()
                return
            self._calibration_message = name + ": not implemented yet"
            LOGGER.info("%s calibration is not implemented yet", name)
        self._redraw = True

    def _back_calibration(self):
        self._calibration_dialog = False
        self.buttons = self._settings_buttons
        self._redraw = True

    def _exit_settings(self):
        self._plot.set_calibration(self.calibration_settings['scale'])
        self.mode = "live"
        self.buttons = self._live_buttons
        if self.camera is not None:
            if self._settings_dirty:
                from .camera import CameraSettings
                values = dict(self._camera_settings,
                              roi=tuple(self.calibration_settings['sensor_area']))
                self.camera.request_reconfigure(CameraSettings(**values))
            else:
                self.camera.request_resume()
        self._redraw = True

    def _poll_settings(self):
        if self.mode == "settings" and self.settings_view.poll():
            self._redraw = True

    @staticmethod
    def _settings_row_rect(index):
        return pygame.Rect(8, 32 + index * 37, 464, 35)

    def _edit_setting(self, index):
        if index not in range(4):
            return
        from .config import CAMERA_MODES, maximum_frame_rate
        keys = ('frame_rate', 'resolution', 'exposure_us', 'frame_averaging')
        self._settings_dialog = keys[index]
        self._settings_message = ''
        self._settings_pressed = None
        self._slider_dragging = False
        self._settings_buttons = self.buttons
        if self._settings_dialog == 'resolution':
            current = tuple(self._camera_settings['resolution'])
            self._resolution_rows = [
                (size, fps, pygame.Rect(64, 62 + row * 46, 352, 40))
                for row, (size, fps) in enumerate(CAMERA_MODES)]
            self._resolution_selected = next(
                (row for row, (size, _, _) in enumerate(self._resolution_rows)
                 if size == current), 0)
        else:
            if self._settings_dialog == 'frame_rate':
                self._settings_min = 1
                self._settings_max = maximum_frame_rate(self._camera_settings['resolution'])
                self._settings_value = round(self._camera_settings['frame_rate'])
            elif self._settings_dialog == 'exposure_us':
                self._settings_min = 0
                self._settings_max = max(1, int(1000 / self._camera_settings['frame_rate']))
                exposure = self._camera_settings['exposure_us']
                self._settings_value = 0 if exposure is None else round(exposure / 1000)
            else:
                self._settings_min, self._settings_max = 1, 10
                self._settings_value = self._camera_settings['frame_averaging']
            self._settings_value = max(self._settings_min,
                                       min(self._settings_max, self._settings_value))
        self.buttons = [('Accept', pygame.Rect(64, 224, 172, 48), self._accept_setting),
                        ('Cancel', pygame.Rect(244, 224, 172, 48), self._cancel_setting)]
        self._redraw = True

    def _setting_slider_rect(self):
        return pygame.Rect(72, 140, 336, 8)

    def _set_slider_position(self, position):
        track = self._setting_slider_rect()
        fraction = max(0.0, min(1.0, (position[0] - track.left) / track.width))
        self._settings_value = round(
            self._settings_min + fraction * (self._settings_max - self._settings_min))
        self._redraw = True

    def _cancel_setting(self):
        self._settings_dialog = None
        self._settings_message = ''
        self.buttons = self._settings_buttons
        self._redraw = True

    def _accept_setting(self):
        from .config import maximum_frame_rate, validate
        updated = deepcopy(self._camera_settings)
        calibration = deepcopy(self.calibration_settings)
        calibration_changed = False
        key = self._settings_dialog
        if key == 'resolution':
            resolution = self._resolution_rows[self._resolution_selected][0]
            updated['resolution'] = resolution
            updated['frame_rate'] = min(updated['frame_rate'], maximum_frame_rate(resolution))
            if resolution != tuple(self._camera_settings['resolution']):
                calibration.update(scale={}, channel_ranges={},
                                   sensor_area=(0, 0, resolution[0], resolution[1]))
                calibration_changed = calibration != self.calibration_settings
        elif key == 'frame_rate':
            updated[key] = self._settings_value
        elif key == 'exposure_us':
            updated[key] = None if self._settings_value == 0 else self._settings_value * 1000
        elif key == 'frame_averaging':
            updated[key] = self._settings_value
        else:
            return
        maximum_exposure = int(1_000_000 / updated['frame_rate'])
        if updated['exposure_us'] is not None:
            updated['exposure_us'] = min(updated['exposure_us'], maximum_exposure)
        try:
            validate({'camera': updated, 'calibration': calibration})
            if self._on_camera_settings_changed is not None:
                self._on_camera_settings_changed(
                    updated, calibration if calibration_changed else None)
        except (OSError, ValueError) as exc:
            LOGGER.exception('Could not save camera setting')
            self._settings_message = str(exc)
            self._redraw = True
            return
        self._camera_settings = updated
        if calibration_changed:
            self.calibration_settings.clear()
            self.calibration_settings.update(calibration)
            if self.camera is not None:
                self.camera.set_calibration(self.calibration_settings)
        self.settings_view.update_camera(self._camera_settings)
        self._settings_dirty = True
        self._cancel_setting()

    def _open_review(self):
        self._live_view = (self._plot, self._camera_bar)
        self.review.refresh()
        self._review_list()
        if self.camera is not None:
            self.camera.request_pause()

    def _review_list(self):
        self._peak_dialog = False
        self._peak_touch = None
        self.review.cancel_filter()
        self.review.cancel_blackbody()
        self._blackbody = False
        self._blackbody_label = None
        self.mode = "review"
        self.buttons = [("Display", pygame.Rect(8, 264, 149, 48), self._display_capture),
                        ("Rename", pygame.Rect(165, 264, 150, 48), self._rename_spectrum),
                        ("Back", pygame.Rect(323, 264, 149, 48), self._exit_review)]
        self._redraw = True

    def _rename_spectrum(self):
        if self.review.future is not None:
            return
        if self.review.selected is None:
            self.review.message = 'Select a capture first'
            self._redraw = True
            return
        self._rename_path = self.review.entries[self.review.selected]
        self.mode = 'rename'
        self._capture_name = ''
        self._capture_upper = False
        self._capture_message = 'Loading name...'
        self._rename_saving = False
        self._rename_ready = False
        self._rename_future = self.review.read_name(self._rename_path)
        self._capture_keyboard()
        self._redraw = True

    def _accept_rename(self):
        if not self._rename_ready or self._rename_future is not None:
            return
        if not self._capture_name.strip():
            self._capture_message = 'Enter a name'
        else:
            self._rename_saving = True
            self._rename_future = self.review.rename(self._rename_path, self._capture_name.strip())
            self._capture_message = 'Saving...'
            self.buttons = []
        self._redraw = True

    def _cancel_rename(self):
        if self._rename_saving and self._rename_future is not None:
            return
        if self._rename_future is not None:
            self._rename_future.cancel()
            self._rename_future = None
        self.review.message = ''
        self._review_list()

    def _poll_rename(self):
        if self._rename_future is None or not self._rename_future.done():
            return
        future, self._rename_future = self._rename_future, None
        try:
            name = future.result()
        except Exception:
            LOGGER.exception('Could not rename spectrum')
            self._capture_message = 'Save failed. Retry or cancel.' if self._rename_saving else 'Cannot load spectrum'
            self._capture_keyboard()
        else:
            if self._rename_saving:
                self.review.names[self._rename_path] = name
                self._cancel_rename()
            else:
                self._capture_name = name
                self._rename_ready = True
                self._capture_message = 'Rename spectrum'
        self._redraw = True

    def _exit_review(self):
        if self.review.future is not None:
            self.review.future.cancel()
            self.review.future = None
        if self._scale_active or self._sensor_active or self._channel_active:
            self._scale_active = False
            self._sensor_active = False
            self._channel_active = False
            self._sensor = None
            self._channel = None
            self._plot, self._camera_bar = self._live_view
            self.mode = "settings"
            self.buttons = self._settings_buttons
            self._peaks = None
            self._redraw = True
            return
        self.mode = "live"
        self.buttons = self._live_buttons
        self._plot, self._camera_bar = self._live_view
        if self.camera is not None:
            self.camera.request_resume()
        self._redraw = True

    def _poll_review(self):
        if self.mode == 'saved' and self._scale_save_future is not None:
            if self._scale_save_future.done():
                future, self._scale_save_future = self._scale_save_future, None
                try:
                    future.result()
                except Exception:
                    LOGGER.exception('Could not save scale calibration to capture')
                    self._scale_save_failed()
                else:
                    self._scale_save_message = ''
                    self._review_list()
                self._redraw = True
            return
        if self.mode in ("review", "saved", "channel") and self.review.poll_names():
            self._redraw = True
        if self.mode == "saved":
            loading = self.review.filter_future is not None
            frame = self.review.poll_filter()
            if frame is not None:
                self._apply_filter(frame)
            elif loading and self.review.filter_future is None:
                self._channels = set(self.review.filter_channel)
                self._redraw = True
            fitting = self.review.blackbody_future is not None
            fit = self.review.poll_blackbody()
            if fit is not None and self._blackbody:
                self._plot.set_fit(fit.intensity)
                self._blackbody_label = 'T: {:,.0f} K'.format(fit.temperature)
                self._redraw = True
            elif fitting and self.review.blackbody_future is None:
                if self._blackbody:
                    self._blackbody_label = 'Fit unavailable'
                    self._mode_message = self.review.blackbody_error or 'Black Body fit failed'
                self._redraw = True
        if self.mode != "review":
            return
        was_loading = self.review.future is not None
        frame = self.review.poll()
        if was_loading and self.review.future is None:
            self._redraw = True
        if frame is not None:
            if self._channel_active:
                from .channel import ChannelCalibration
                self._channel = ChannelCalibration(
                    frame, self.calibration_settings['channel_ranges'])
                self.mode = 'channel'
                self.buttons = [
                    ('Accept', pygame.Rect(8, 272, 149, 40), self._accept_channel),
                    ('Reset', pygame.Rect(165, 272, 150, 40), self._reset_channel),
                    ('Cancel', pygame.Rect(323, 272, 149, 40), self._exit_review)]
                self._redraw = True
                return
            from .plot import SpectrumPlot
            from .camera import BAR_SIZE

            self._channels = {"Red", "Green", "Blue"}
            self._absorption = False
            self._blackbody = False
            self._blackbody_label = None
            self._plot = SpectrumPlot(frame.roi)
            self.mode = "saved"
            self._update_plot(frame)
            self._camera_bar = pygame.transform.flip(pygame.image.frombuffer(frame.bar, BAR_SIZE, "RGB"), True, False)
            self.mode = "saved"
            self._reset_peaks(frame.intensity)
            if self._scale_active:
                self._scale_save_message = ''
                self._saved_buttons = [("Modes", pygame.Rect(8, 264, 149, 48), self._ask_filter),
                                       ("Reset", pygame.Rect(165, 264, 150, 48), self._reset_scale),
                                       ("Back", pygame.Rect(323, 264, 149, 48), self._finish_scale)]
                self.buttons = self._saved_buttons
                return
            self._saved_buttons = [
                ("Delete", pygame.Rect(8, 264, 149, 48), self._ask_delete),
                ("Modes", pygame.Rect(165, 264, 150, 48), self._ask_filter),
                ("Back", pygame.Rect(323, 264, 149, 48), self._review_list)]
            self.buttons = self._saved_buttons

    def _display_capture(self):
        if self._channel_active:
            from .review import load_channels
            self.review.display(load_channels)
        else:
            self.review.display()

    def _open_sensor(self):
        from .camera import CameraSettings
        if self.camera is None:
            self._sensor_active = False
            self.mode = 'settings'
            self.buttons = self._settings_buttons
            self.settings_view.message = 'Camera unavailable'
            self._redraw = True
            return
        self.mode = 'sensor'
        self._sensor = None
        self._sensor_paused = False
        settings = CameraSettings(**self._camera_settings, roi=tuple(
            self.calibration_settings['sensor_area']))
        self.camera.request_sensor_preview(settings)
        self._sensor_buttons()
        self._redraw = True

    def _sensor_buttons(self):
        self.buttons = [('Accept', pygame.Rect(8, 264, 149, 48), self._accept_sensor),
                        ('Resume' if self._sensor_paused else 'Pause',
                         pygame.Rect(165, 264, 150, 48), self._toggle_sensor_pause),
                        ('Cancel', pygame.Rect(323, 264, 149, 48), self._cancel_sensor)]

    def _toggle_sensor_pause(self):
        if self._sensor is None:
            return
        self._sensor_paused = not self._sensor_paused
        self._sensor.start = None
        if self._sensor_paused:
            self.camera.request_pause()
        else:
            self.camera.poll_sensor_preview()  # Discard any frame queued during stop.
            self.camera.request_resume()
        self._sensor_buttons()
        self._redraw = True

    def _cancel_sensor(self):
        roi = self.calibration_settings['sensor_area']
        self.camera.request_end_sensor_preview(roi)
        self._exit_review()

    def _reset_channel(self):
        self._channel.reset()
        self._redraw = True

    def _accept_channel(self):
        from .config import validate
        updated = dict(self.calibration_settings,
                       channel_ranges=dict(self._channel.ranges))
        try:
            validate({'camera': self._camera_settings, 'calibration': updated})
            if self._on_calibration_changed is not None:
                self._on_calibration_changed(updated)
        except (OSError, ValueError):
            LOGGER.exception('Could not save channel calibration')
            self._channel.message = 'Could not save channel ranges'
            self._redraw = True
            return
        self.calibration_settings.update(updated)
        if self.camera is not None:
            self.camera.set_calibration(self.calibration_settings)
        self._exit_review()

    def _accept_sensor(self):
        if self._sensor is None:
            return
        from .config import validate
        roi = self._sensor.roi
        updated = dict(self.calibration_settings, sensor_area=roi)
        try:
            validate({'camera': self._camera_settings, 'calibration': updated})
            if self._on_calibration_changed is not None:
                self._on_calibration_changed(updated)
        except ValueError:
            LOGGER.exception('Could not save sensor area')
            self._sensor.message = 'ROI must fit image; width at least 462 px'
            self._redraw = True
            return
        except OSError:
            LOGGER.exception('Could not save sensor area')
            self._sensor.message = 'Could not save settings. Retry or cancel.'
            self._redraw = True
            return
        self.calibration_settings.update(updated)
        if self.camera is not None:
            self.camera.set_calibration(self.calibration_settings)
        self._cancel_sensor()

    def _update_plot(self, frame):
        from .plot import SpectrumPlot
        maximum = frame.maximum or ((frame.roi[3] - frame.roi[1]) * 255 * 3)
        if self._plot.roi != tuple(frame.roi) or self._plot.maximum != maximum:
            self._plot = SpectrumPlot(frame.roi, maximum)
            self._redraw = True
        calibration = frame.calibration if self.mode == 'saved' else self.calibration_settings
        if self._plot.set_calibration({} if self._scale_active else calibration['scale']):
            self._redraw = True
        self._plot.update(frame.intensity)

    def _reset_peaks(self, intensity):
        from .scale import PeakSelection
        self._peaks = PeakSelection(intensity,
                                    self._plot.AREA.move(self.spectrum_rect.topleft),
                                    self._plot.maximum, self._plot.roi[0], self._absorption)
        self._review_peak_label = None
        self._spectrum_intensity = intensity

    def _select_peak(self, position):
        index = self._peaks.select(position)
        self._peak_message = ("No valleys found" if self._absorption else "No peaks found") if index is None else "Pixel %s" % (index)
        if not self._scale_active:
            if index is not None and self._plot.scale is not None:
                self._peak_message = '%.1f nm' % (self._plot.scale.wavelength(index))
            self._review_peak_label = self._peak_message
            self._redraw = True
            return
        self._peak_dialog = True
        if index in self.calibration_settings["scale"]:
            self.buttons = [("Modify", pygame.Rect(64, 248, 112, 48), self._label_peak),
                            ("Delete", pygame.Rect(184, 248, 112, 48), self._delete_peak_label),
                            ("Back", pygame.Rect(304, 248, 112, 48), self._back_peak)]
        else:
            self.buttons = [("Label", pygame.Rect(64, 248, 172, 48), self._label_peak),
                            ("Back", pygame.Rect(244, 248, 172, 48), self._back_peak)]
        self._redraw = True

    def _delete_peak_label(self):
        if self._peaks.selected is not None:
            pixel = int(self._peaks.indices[self._peaks.selected])
            if not self._update_scale(pixel, None):
                return
        self._back_peak()

    def _label_peak(self):
        if self._peaks.selected is not None:
            self._label_pixel = int(self._peaks.indices[self._peaks.selected])
            existing = self.calibration_settings["scale"].get(self._label_pixel)
            self._label_input = "" if existing is None else format(existing, ".12f").rstrip("0").rstrip(".")
            self._label_error = ""
            self._keypad_open = True
            self._peak_buttons = self.buttons
            keys = ("1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "Bksp")
            self.buttons = [(key, pygame.Rect(120 + (i % 3) * 82, 76 + (i // 3) * 44, 76, 40),
                             lambda value=key: self._label_key(value)) for i, key in enumerate(keys)]
            self.buttons.extend([
                ("Accept", pygame.Rect(120, 258, 117, 44), self._accept_label),
                ("Cancel", pygame.Rect(243, 258, 117, 44), self._cancel_label)])
            self._redraw = True

    def _label_key(self, key):
        if key == "Bksp":
            self._label_input = self._label_input[:-1]
        elif key == "." and "." not in self._label_input and len(self._label_input) < 12:
            self._label_input = (self._label_input or "0") + "."
        elif key in "0123456789" and len(key) == 1 and len(self._label_input) < 12:
            self._label_input += key
        self._label_error = ""
        self._redraw = True

    def _accept_label(self):
        import math
        try:
            value = float(self._label_input)
            if not math.isfinite(value) or value < 0:
                raise ValueError
        except ValueError:
            self._label_error = "Enter a numeric value"
            self._redraw = True
            return
        if not self._update_scale(self._label_pixel, value):
            return
        self._keypad_open = False
        self._back_peak()

    def _update_scale(self, pixel, value):
        labels = self.calibration_settings["scale"].copy()
        if value is None:
            labels.pop(pixel, None)
        else:
            labels[pixel] = value
        updated = dict(self.calibration_settings, scale=labels)
        try:
            if self._on_calibration_changed is not None:
                self._on_calibration_changed(updated)
        except (OSError, ValueError):
            LOGGER.exception("Could not save calibration settings")
            self._label_error = "Could not save settings"
            self._peak_message = "Could not save settings"
            self._redraw = True
            return False
        self.calibration_settings.update(updated)
        if self.camera is not None:
            self.camera.set_calibration(self.calibration_settings)
        return True

    def _reset_scale(self):
        updated = dict(self.calibration_settings, scale={})
        try:
            if self._on_calibration_changed is not None:
                self._on_calibration_changed(updated)
        except (OSError, ValueError):
            LOGGER.exception("Could not reset calibration settings")
            self._review_peak_label = "Could not save settings"
            self._redraw = True
            return
        self.calibration_settings.update(updated)
        if self.camera is not None:
            self.camera.set_calibration(self.calibration_settings)
        if self._peaks is not None:
            self._peaks.selected = None
        self._redraw = True

    def _finish_scale(self):
        if self._scale_save_future is not None:
            return
        try:
            self._scale_save_future = self.review.save_scale(self.calibration_settings['scale'])
        except Exception:
            LOGGER.exception('Could not start saving scale calibration to capture')
            self._scale_save_failed()
        else:
            self._scale_save_message = 'Saving calibration...'
            self.buttons = []
            self._peak_touch = None
        self._redraw = True

    def _scale_save_failed(self):
        self._scale_save_message = 'Save failed. Retry or cancel.'
        self.buttons = [('Retry', pygame.Rect(8, 264, 228, 48), self._finish_scale),
                        ('Cancel', pygame.Rect(244, 264, 228, 48), self._cancel_scale_save)]

    def _cancel_scale_save(self):
        self._scale_save_message = ''
        self._review_list()

    def _cancel_label(self):
        self._keypad_open = False
        self.buttons = self._peak_buttons
        self._redraw = True

    def _back_peak(self):
        self._peak_dialog = False
        self._peaks.selected = None
        self.buttons = self._saved_buttons
        self._redraw = True

    def _ask_filter(self):
        self._filter_dialog = True
        self._mode_message = ''
        self._mode_buttons()
        self._redraw = True

    def _mode_buttons(self):
        names = ['Red', 'Green', 'Blue', 'Absorption' if self._absorption else 'Emission']
        if not self._scale_active:
            names.append('Black Body')
        self.buttons = [(name, pygame.Rect(64, 62 + i * 34, 352, 30),
                         lambda channel=name: self._choose_filter(channel))
                        for i, name in enumerate(names)]
        self.buttons.append(('Back', pygame.Rect(64, 238, 352, 40), self._back_filter))

    def _choose_filter(self, channel):
        if channel in ('Emission', 'Absorption'):
            self._absorption = not self._absorption
            self._reset_peaks(self._spectrum_intensity)
        elif channel == 'Black Body':
            if self._blackbody:
                self._blackbody = False
                self._blackbody_label = None
                self.review.cancel_blackbody()
                self._plot.set_fit()
            elif self._plot.scale is None:
                self._mode_message = 'Black Body needs wavelength calibration'
            else:
                self._blackbody = True
                self._start_blackbody_fit()
        else:
            self._channels.symmetric_difference_update({channel})
            frame = self.review.request_channels(tuple(name for name in ('Red', 'Green', 'Blue')
                                                       if name in self._channels))
            if frame is not None:
                self._apply_filter(frame)
        self._mode_buttons()
        self._redraw = True

    def _apply_filter(self, frame):
        from .camera import BAR_SIZE
        self._update_plot(frame)
        self._reset_peaks(frame.intensity)
        self._camera_bar = pygame.transform.flip(pygame.image.frombuffer(frame.bar, BAR_SIZE, "RGB"), True, False)
        if self._blackbody:
            self._start_blackbody_fit()
        self._redraw = True

    def _start_blackbody_fit(self):
        self._plot.set_fit()
        self._blackbody_label = 'Fitting...'
        self._mode_message = ''
        self.review.request_blackbody(self._spectrum_intensity, self._plot.roi, self._plot.scale)

    def _back_filter(self):
        self._filter_dialog = False
        self.buttons = self._saved_buttons
        self._redraw = True

    def _ask_delete(self):
        self._delete_dialog = True
        self._delete_message = "Delete this spectrum?"
        self.buttons = [("Confirm", pygame.Rect(64, 168, 172, 48), self._confirm_delete),
                        ("Cancel", pygame.Rect(244, 168, 172, 48), self._cancel_delete)]
        self._redraw = True

    def _cancel_delete(self):
        self._delete_dialog = False
        self.buttons = self._saved_buttons
        self._redraw = True

    def _confirm_delete(self):
        if self.review.delete_loaded():
            self._delete_dialog = False
            self._review_list()
        else:
            LOGGER.error("%s", self.review.message)
            self._delete_message = "Delete failed. Retry or cancel."
        self._redraw = True

    def _pointer_motion(self, pointer, position):
        if (self.mode == 'settings' and self._pointer == pointer
                and self._settings_dialog not in (None, 'resolution')
                and self._slider_dragging):
            self._set_slider_position(position)
        if self.mode == 'channel' and self._pointer == pointer and self._channel.dragging is not None:
            self._channel.drag(position)
            self._redraw = True
        if (self.mode == 'sensor' and self._sensor is not None and self._sensor_paused
                and self._pointer == pointer and self._sensor.start is not None):
            self._sensor.drag(position)
            self._redraw = True
        if self.mode == "review" and self._pointer == pointer and self._list_start is not None:
            start_y, offset = self._list_start
            rows = int((start_y - position[1]) / self.review.ROW_HEIGHT)
            self.review.offset = max(0, min(max(0, len(self.review.entries) - self.review.VISIBLE), offset + rows))
            self._redraw = True

    def _button_at(self, position):
        return next((i for i, (_, rect, _) in enumerate(self.buttons)
                     if rect.collidepoint(position)), None)

    def _handle_pointer(self, event):
        """Activate on release inside the original button; ignore extra fingers."""
        if event.type == pygame.MOUSEWHEEL and self.mode == "review":
            self.review.scroll(-event.y)
            self._redraw = True
            return
        if event.type == pygame.MOUSEMOTION and not getattr(event, "touch", False):
            self._pointer_motion("mouse", event.pos)
            return
        if event.type == pygame.FINGERMOTION:
            self._pointer_motion((event.touch_id, event.finger_id),
                                 (event.x * 480, event.y * 320))
            return
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            # SDL also emits mouse events for touches. Handle each touch once.
            if event.button != 1 or getattr(event, "touch", False):
                return
            pointer = "mouse"
            position = event.pos
            down = event.type == pygame.MOUSEBUTTONDOWN
        elif event.type in (pygame.FINGERDOWN, pygame.FINGERUP):
            pointer = (event.touch_id, event.finger_id)
            position = (event.x * SCREEN_SIZE[0], event.y * SCREEN_SIZE[1])
            down = event.type == pygame.FINGERDOWN
        else:
            return

        self._pointer_event(pointer, position, down)

    def _pointer_event(self, pointer, position, down):
        """Shared button handling for desktop events and polled hardware touch."""
        if self.mode == 'saved' and self._scale_save_future is not None:
            self._pointer = self._pressed = None
            return
        if self.mode in ('shutdown', 'capture', 'rename') or (self.mode == 'saved' and self._scale_save_message):
            if down and self._pointer is None:
                self._pointer = pointer
                self._pressed = self._button_at(position)
            elif not down and self._pointer == pointer:
                selected = self._pressed
                self._pointer = self._pressed = None
                if selected is not None and self._button_at(position) == selected:
                    self.buttons[selected][2]()
            return
        if down and self._pointer is None:
            self._pointer = pointer
            self._pressed = self._button_at(position)
            if self.mode == 'settings' and self._settings_dialog:
                if self._settings_dialog == 'resolution':
                    self._settings_pressed = next(
                        (i for i, (_, _, rect) in enumerate(self._resolution_rows)
                         if rect.collidepoint(position)), None)
                elif self._setting_slider_rect().inflate(24, 36).collidepoint(position):
                    self._slider_dragging = True
                    self._set_slider_position(position)
            elif self.mode == 'settings' and not self._calibration_dialog:
                self._settings_pressed = next(
                    (i for i in range(4) if self._settings_row_rect(i).collidepoint(position)), None)
            if self.mode == 'channel' and self._channel.start(position):
                self._redraw = True
            if (self.mode == 'sensor' and self._sensor is not None and self._sensor_paused
                    and self._sensor.rect.collidepoint(position)):
                self._sensor.start = self._sensor.point(position)
            if (self.mode == "saved" and self._peaks is not None
                    and not (self._peak_dialog or self._filter_dialog or self._delete_dialog)
                    and self._plot.AREA.move(self.spectrum_rect.topleft).collidepoint(position)):
                self._peak_touch = position
            if self._calibration_dialog:
                self._calibration_pressed = next((i for i, (_, rect) in enumerate(self._calibration_rows)
                                                 if rect.collidepoint(position)), None)
            if self.mode == "review" and 34 <= position[1] < 244:
                self._list_start = (position[1], self.review.offset)
        elif not down and self._pointer == pointer:
            if self.mode == 'settings' and self._settings_dialog:
                if self._settings_dialog == 'resolution' and self._settings_pressed is not None:
                    index = self._settings_pressed
                    if self._resolution_rows[index][2].collidepoint(position):
                        self._resolution_selected = index
                        self._redraw = True
                    self._settings_pressed = None
                    self._pointer = self._pressed = None
                    return
                if self._slider_dragging:
                    self._set_slider_position(position)
                    self._slider_dragging = False
                    self._pointer = self._pressed = None
                    return
            elif self.mode == 'settings' and self._settings_pressed is not None:
                index = self._settings_pressed
                self._settings_pressed = None
                self._pointer = self._pressed = None
                if self._settings_row_rect(index).collidepoint(position):
                    self._edit_setting(index)
                return
            if self.mode == 'channel' and self._channel.dragging is not None:
                self._channel.drag(position)
                self._channel.stop()
                self._pointer = self._pressed = None
                self._redraw = True
                return
            if self.mode == 'sensor' and self._sensor is not None and self._sensor.start is not None:
                if self._sensor.point(position) != self._sensor.start:
                    self._sensor.drag(position)
                self._sensor.start = None
                self._pointer = self._pressed = None
                self._redraw = True
                return
            if self._peak_touch is not None:
                self._peak_touch = None
                self._pointer = self._pressed = None
                if self._plot.AREA.move(self.spectrum_rect.topleft).collidepoint(position):
                    self._select_peak(position)
                return
            if self._calibration_dialog and self._calibration_pressed is not None:
                index = self._calibration_pressed
                if self._calibration_rows[index][1].collidepoint(position):
                    self._calibration_selected = index
                    self._calibration_message = "Calibration mode"
                    self._redraw = True
                self._calibration_pressed = None
            if self._list_start is not None:
                if abs(position[1] - self._list_start[0]) < 10 and 34 <= position[1] < 244:
                    index = self.review.offset + int((position[1] - 34) // self.review.ROW_HEIGHT)
                    if index < len(self.review.entries):
                        self.review.selected = index
                self._list_start = None
                self._redraw = True
            selected = self._pressed
            self._pointer = self._pressed = None
            if selected is not None and self._button_at(position) == selected:
                label, _, callback = self.buttons[selected]
                if callback is not None:
                    callback()
                    if self.mode != "live":
                        self._redraw = True
                else:
                    LOGGER.info("%s is not implemented yet", label)

    def draw(self, surface, font):
        """Draw cached spectrum axes, trace, camera slice, and touch controls."""
        surface.fill(BACKGROUND)
        if self.mode in ("capture", "rename"):
            text = font.render(self._capture_message, True, TEXT)
            surface.blit(text, (10, 5))
            pygame.draw.rect(surface, PANEL, (8, 28, 464, 30))
            text = font.render(self._capture_name, True, TEXT)
            previous_clip = surface.get_clip()
            surface.set_clip(previous_clip.clip(pygame.Rect(12, 30, 456, 26)))
            surface.blit(text, (min(12, 468 - text.get_width()), 32))
            surface.set_clip(previous_clip)
            for i, (label, rect, _) in enumerate(self.buttons):
                pygame.draw.rect(surface, PRESSED if self._pressed == i else BUTTON, rect, border_radius=4)
                key_font = font
                if label in ('⇧', '␣', '⌫'):
                    if not hasattr(self, '_keyboard_symbol_font'):
                        self._keyboard_symbol_font = pygame.font.SysFont('dejavusans,applesymbols,arial', 24)
                    key_font = self._keyboard_symbol_font
                    if label == '⌫':
                        if not hasattr(self, '_keyboard_backspace_font'):
                            self._keyboard_backspace_font = pygame.font.SysFont('dejavusans,applesymbols,arial', 18)
                        key_font = self._keyboard_backspace_font
                text = key_font.render(label, True, TEXT)
                surface.blit(text, text.get_rect(center=rect.center))
            return
        if self.mode == 'shutdown':
            pygame.draw.rect(surface, PANEL, (48, 88, 384, 144), border_radius=8)
            pygame.draw.rect(surface, BORDER, (48, 88, 384, 144), 1, border_radius=8)
            text = font.render(self._shutdown_message, True, TEXT)
            surface.blit(text, text.get_rect(center=(240, 124)))
            for i, (label, rect, _) in enumerate(self.buttons):
                pygame.draw.rect(surface, PRESSED if self._pressed == i else BUTTON, rect, border_radius=6)
                text = font.render(label, True, TEXT)
                surface.blit(text, text.get_rect(center=rect.center))
            return
        if self.mode == "review":
            self._draw_review(surface, font)
        if self.mode == "settings":
            self._draw_settings(surface, font)
        if self.mode == 'sensor':
            if self._sensor is not None:
                self._sensor.draw(surface, font)
            else:
                surface.blit(font.render('Waiting for camera...', True, TEXT), (8, 8))
        if self.mode == 'channel':
            self._channel.draw(surface, font)
        if self.mode in ("live", "saved") and surface.get_clip().colliderect(self.spectrum_rect):
            self._plot.draw(surface, self.spectrum_rect.topleft)
        for rect, label in ((self.camera_slice_rect, "Raw camera slice — placeholder"),):
            if self.mode in ("review", "settings", "sensor", "channel"):
                break
            if not surface.get_clip().colliderect(rect):
                continue
            pygame.draw.rect(surface, PANEL, rect, border_radius=4)
            pygame.draw.rect(surface, BORDER, rect, width=1, border_radius=4)
            text = font.render(label, True, MUTED)
            surface.blit(text, text.get_rect(center=rect.center))
        if self.mode in ("live", "saved") and self._camera_bar is not None:
            surface.blit(self._camera_bar, self.camera_slice_rect.move(1, 1))
        if self.mode == 'saved':
            small = self._font(18)
            rect = pygame.Rect(16, 12, 220 if self._blackbody else 300, 18)
            pygame.draw.rect(surface, PANEL, rect)
            text = self._fit_text(small, self.review.name(self.review.loaded_path), rect.width)
            surface.blit(text, rect.topleft)
        if self.mode == "saved" and not self._scale_active and self._review_peak_label:
            small = self._font(18)
            text = small.render(self._review_peak_label, True, "#ffd166")
            surface.blit(text, text.get_rect(topright=(374 if self._blackbody else self.spectrum_rect.right - 8,
                                                      self.spectrum_rect.top + 5)))
        if self.mode == 'saved' and self._blackbody_label:
            small = self._font(18)
            text = small.render(self._blackbody_label, True, '#ffd166')
            surface.blit(text, text.get_rect(topright=(self.spectrum_rect.right - 8,
                                                       self.spectrum_rect.top + 5)))
        if self.mode == "saved":
            if self._scale_active:
                self._draw_peak_labels(surface)
            marker = self._peaks.marker if self._peaks is not None else None
            if marker is not None:
                area = self._plot.AREA.move(self.spectrum_rect.topleft)
                pygame.draw.line(surface, "#ffd166", (marker[0], area.top),
                                 (marker[0], area.bottom - 1))
                pygame.draw.circle(surface, "#ffd166", marker, 5, 2)
            if self._peak_dialog and not self._keypad_open:
                # Keep the plot and selected peak unobscured above the dialog.
                pygame.draw.rect(surface, PANEL, (48, 200, 384, 112), border_radius=8)
                pygame.draw.rect(surface, BORDER, (48, 200, 384, 112), 1, border_radius=8)
                small = self._font(20)
                text = small.render(self._peak_message, True, TEXT)
                surface.blit(text, text.get_rect(center=(240, 224)))
            if self._scale_save_message:
                rect = pygame.Rect(48, 176, 384, 32)
                pygame.draw.rect(surface, PANEL, rect, border_radius=4)
                text = self._font(18).render(self._scale_save_message, True, '#ffd166')
                surface.blit(text, text.get_rect(center=rect.center))
        if self._delete_dialog:
            for label, rect, _ in self._saved_buttons:
                pygame.draw.rect(surface, BUTTON, rect, border_radius=6)
                text = font.render(label, True, TEXT)
                surface.blit(text, text.get_rect(center=rect.center))
            shade = pygame.Surface(SCREEN_SIZE, pygame.SRCALPHA)
            shade.fill((0, 0, 0, 160))
            surface.blit(shade, (0, 0))
            pygame.draw.rect(surface, PANEL, (48, 88, 384, 144), border_radius=8)
            pygame.draw.rect(surface, BORDER, (48, 88, 384, 144), 1, border_radius=8)
            text = font.render(self._delete_message, True, TEXT)
            surface.blit(text, text.get_rect(center=(240, 114)))
            small = self._font(18)
            name = self.review.loaded_path.name if self.review.loaded_path else ""
            text = small.render(name, True, MUTED)
            surface.blit(text, text.get_rect(center=(240, 142)))
        if self._calibration_dialog:
            self._draw_calibration_dialog(surface, font)
        if self._settings_dialog:
            self._draw_setting_dialog(surface, font)
        if self._filter_dialog:
            for label, rect, _ in self._saved_buttons:
                pygame.draw.rect(surface, BUTTON, rect, border_radius=6)
                text = font.render(label, True, TEXT)
                surface.blit(text, text.get_rect(center=rect.center))
            shade = pygame.Surface(SCREEN_SIZE, pygame.SRCALPHA)
            shade.fill((0, 0, 0, 160))
            surface.blit(shade, (0, 0))
            pygame.draw.rect(surface, PANEL, (48, 28, 384, 260), border_radius=8)
            small = self._font(19)
            title = self._mode_message or self.review.message or "Modes"
            text = small.render(title[:52], True, TEXT)
            surface.blit(text, text.get_rect(center=(240, 46)))
        if self._keypad_open:
            self._draw_label_keypad(surface)
        for i, (label, rect, _) in enumerate(self.buttons):
            if not surface.get_clip().colliderect(rect):
                continue
            color = PRESSED if self._pressed == i else BUTTON
            if self._filter_dialog and label in ("Red", "Green", "Blue"):
                color = PRESSED if label in self._channels else BACKGROUND
            if self._filter_dialog and label == 'Black Body':
                color = PRESSED if self._blackbody else BACKGROUND
            pygame.draw.rect(surface, color, rect, border_radius=6)
            text = font.render(label, True, TEXT)
            surface.blit(text, text.get_rect(center=rect.center))

    def _draw_label_keypad(self, surface):
        shade = pygame.Surface(SCREEN_SIZE, pygame.SRCALPHA)
        shade.fill((0, 0, 0, 160))
        surface.blit(shade, (0, 0))
        pygame.draw.rect(surface, PANEL, (108, 8, 264, 304), border_radius=8)
        pygame.draw.rect(surface, BORDER, (108, 8, 264, 304), 1, border_radius=8)
        small = self._font(19)
        title = self._label_error or "Label pixel %s" % self._label_pixel
        text = small.render(title, True, TEXT)
        surface.blit(text, text.get_rect(center=(240, 22)))
        pygame.draw.rect(surface, BACKGROUND, (120, 36, 240, 32), border_radius=4)
        text = self._font(26).render(self._label_input or "0", True, TEXT)
        surface.blit(text, text.get_rect(midright=(352, 52)))

    def _draw_peak_labels(self, surface):
        area = self._plot.AREA.move(self.spectrum_rect.topleft)
        font = self._font(18)
        occupied = []
        for sensor_pixel, value in sorted(self.calibration_settings["scale"].items()):
            pixel = sensor_pixel - self._plot.roi[0]
            if not 0 <= pixel < len(self._spectrum_intensity):
                continue
            x = area.right - 1 - round(pixel * (area.width - 1) / (len(self._spectrum_intensity) - 1))
            y = area.bottom - 1 - round(min(self._plot.maximum, max(0, self._spectrum_intensity[pixel]))
                                       * (area.height - 1) / self._plot.maximum)
            text = font.render(format(value, ".12g"), True, "#ffd166")
            rect = text.get_rect(midbottom=(x, y - 7))
            rect.clamp_ip(area)
            while any(rect.colliderect(other) for other in occupied) and rect.bottom + rect.height <= area.bottom:
                rect.y += rect.height
            occupied.append(rect)
            pygame.draw.line(surface, "#ffd166", (x, y), rect.midbottom)
            pygame.draw.circle(surface, "#ffd166", (x, y), 4, 1)
            pygame.draw.rect(surface, PANEL, rect)
            surface.blit(text, rect)

    def _draw_calibration_dialog(self, surface, font):
        for label, rect, _ in self._settings_buttons:
            pygame.draw.rect(surface, BUTTON, rect, border_radius=6)
            text = font.render(label, True, TEXT)
            surface.blit(text, text.get_rect(center=rect.center))
        shade = pygame.Surface(SCREEN_SIZE, pygame.SRCALPHA)
        shade.fill((0, 0, 0, 160))
        surface.blit(shade, (0, 0))
        pygame.draw.rect(surface, PANEL, (48, 32, 384, 256), border_radius=8)
        pygame.draw.rect(surface, BORDER, (48, 32, 384, 256), 1, border_radius=8)
        small = self._font(20)
        text = small.render(self._calibration_message, True, TEXT)
        surface.blit(text, text.get_rect(center=(240, 56)))
        for i, (name, rect) in enumerate(self._calibration_rows):
            pygame.draw.rect(surface, BUTTON if i == self._calibration_selected else BACKGROUND,
                             rect, border_radius=4)
            text = font.render(name, True, TEXT)
            surface.blit(text, text.get_rect(midleft=(rect.left + 12, rect.centery)))

    @staticmethod
    def _fit_text(font, value, width):
        text = value
        while text and font.size(text)[0] > width:
            value = value[:-1]
            text = value + '...' if value else ''
        return font.render(text, True, TEXT)

    def _font(self, size):
        font = self._fonts.get(size)
        if font is None:
            font = self._fonts[size] = pygame.font.Font(None, size)
        return font

    def _draw_review(self, surface, font):
        small = self._font(19)
        header = self.review.message or ("Review captures" if self.review.entries else "No captures found")
        if self._scale_active and not self.review.message and self.review.entries:
            header = "Scale calibration: select a capture"
        if self._channel_active and not self.review.message and self.review.entries:
            header = "Channel calibration: select a capture"
        surface.blit(small.render(header[:65], True, TEXT), (8, 10))
        for row, path in enumerate(self.review.entries[self.review.offset:self.review.offset + 5]):
            index = row + self.review.offset
            rect = pygame.Rect(8, 34 + row * 42, 450, 40)
            pygame.draw.rect(surface, BUTTON if index == self.review.selected else PANEL, rect)
            date = self.review.timestamp(path) or path.stem.removeprefix('spectrum-')
            if not self.review.timestamp(path):
                try:
                    from datetime import datetime
                    date = datetime.strptime(date, '%Y-%m-%d-%H-%M-%S').strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    pass
            date_text = small.render(date, True, MUTED)
            date_rect = date_text.get_rect(midright=(rect.right - 8, rect.centery))
            surface.blit(date_text, date_rect)
            text = self._fit_text(font, self.review.name(path), max(0, date_rect.left - 24))
            surface.blit(text, text.get_rect(midleft=(16, rect.centery)))
        if len(self.review.entries) > 5:
            height = max(10, 210 * 5 // len(self.review.entries))
            y = 34 + (210 - height) * self.review.offset // (len(self.review.entries) - 5)
            pygame.draw.rect(surface, MUTED, (464, y, 6, height))

    def _draw_settings(self, surface, font):
        small = self._font(19)
        title = self.settings_view.message or "Settings"
        surface.blit(small.render(title, True, TEXT), (8, 10))
        for index, (name, value) in enumerate(self.settings_view.rows):
            rect = self._settings_row_rect(index)
            pygame.draw.rect(surface, PANEL, rect)
            surface.blit(font.render(name, True, MUTED), (16, rect.y + 9))
            # Fit long SSIDs without allowing values to overlap option names.
            while small.size(value)[0] > 245 and len(value) > 1:
                value = value[:-4] + "..." if len(value) > 4 else value[:-1]
            text = small.render(value, True, TEXT)
            surface.blit(text, text.get_rect(midright=(464, rect.centery)))

    def _draw_setting_dialog(self, surface, font):
        shade = pygame.Surface(SCREEN_SIZE, pygame.SRCALPHA)
        shade.fill((0, 0, 0, 170))
        surface.blit(shade, (0, 0))
        pygame.draw.rect(surface, PANEL, (48, 28, 384, 260), border_radius=8)
        pygame.draw.rect(surface, BORDER, (48, 28, 384, 260), 1, border_radius=8)
        titles = {'frame_rate': 'Frame rate', 'resolution': 'Camera resolution',
                  'exposure_us': 'Exposure time', 'frame_averaging': 'Frame averaging'}
        small = self._font(19)
        title = self._settings_message or titles[self._settings_dialog]
        surface.blit(small.render(title[:54], True, TEXT),
                     small.render(title[:54], True, TEXT).get_rect(center=(240, 47)))
        if self._settings_dialog == 'resolution':
            for index, (size, maximum, rect) in enumerate(self._resolution_rows):
                pygame.draw.rect(surface, BUTTON if index == self._resolution_selected else BACKGROUND,
                                 rect, border_radius=4)
                label = f'{size[0]} x {size[1]}  ({maximum} fps max)'
                text = small.render(label, True, TEXT)
                surface.blit(text, text.get_rect(center=rect.center))
            return
        if self._settings_dialog == 'exposure_us':
            value = 'Auto' if self._settings_value == 0 else f'{self._settings_value} ms'
        elif self._settings_dialog == 'frame_rate':
            value = f'{self._settings_value} fps'
        else:
            value = str(self._settings_value)
        value_text = font.render(value, True, TEXT)
        surface.blit(value_text, value_text.get_rect(center=(240, 100)))
        track = self._setting_slider_rect()
        pygame.draw.rect(surface, BORDER, track, border_radius=4)
        span = self._settings_max - self._settings_min
        fraction = 0 if span == 0 else (self._settings_value - self._settings_min) / span
        x = round(track.left + fraction * track.width)
        pygame.draw.circle(surface, PRESSED, (x, track.centery), 13)
        low = small.render('Auto' if self._settings_dialog == 'exposure_us' else str(self._settings_min),
                           True, MUTED)
        high = small.render(str(self._settings_max), True, MUTED)
        surface.blit(low, low.get_rect(midtop=(track.left, 158)))
        surface.blit(high, high.get_rect(midtop=(track.right, 158)))

    def run(self):
        """Use the directly connected LCD unless a desktop preview is requested."""
        from contextlib import nullcontext

        try:
            with (self.camera if self.camera is not None else nullcontext()):
                if self.fullscreen:
                    self._run_lcd()
                else:
                    self._run_desktop()
        finally:
            self.review.close()
            self.settings_view.close()

    def _timed_draw(self, surface, font):
        started = time.monotonic()
        self.draw(surface, font)
        METRICS.add('draw_ms', (time.monotonic() - started) * 1000)

    def _poll_camera(self):
        if self.camera is not None and self.mode == 'sensor':
            if self._sensor_paused:
                return False
            preview = self.camera.poll_sensor_preview()
            if preview is None:
                return False
            from .sensor import SensorSelection
            roi = (self._sensor.roi if self._sensor is not None else
                   self.calibration_settings['sensor_area'])
            self._sensor = SensorSelection(preview, roi)
            return True
        if self.camera is None or self.mode != "live":
            return False
        frame = self.camera.poll()
        if frame is None:
            return False
        from .camera import BAR_SIZE

        self._camera_bar = pygame.transform.flip(pygame.image.frombuffer(frame.bar, BAR_SIZE, "RGB"), True, False)
        self._update_plot(frame)
        METRICS.add('camera_frames')
        return True

    def _run_lcd(self):
        from contextlib import ExitStack
        import signal

        from .hardware import LCDBackend

        stopping = threading.Event()
        try:
            with ExitStack() as cleanup:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous = signal.signal(signum, lambda *_: stopping.set())
                    cleanup.callback(signal.signal, signum, previous)
                with LCDBackend() as hardware:
                    # Off-screen rendering needs no SDL window, X server, or DRM.
                    pygame.font.init()
                    surface = pygame.Surface(SCREEN_SIZE)
                    font = self._font(22)
                    self._timed_draw(surface, font)
                    hardware.present(surface)
                    last_position = None
                    active = True
                    while not stopping.is_set():
                        iteration_started = time.monotonic()
                        power = hardware.power_event()
                        if power in ('short', 'hold') and self.mode != 'shutdown':
                            active = True if power == 'hold' else not active
                            self._pointer = self._pressed = None
                            self._list_start = None
                            self._peak_touch = None
                            if self._sensor is not None:
                                self._sensor.start = None
                            if self._channel is not None:
                                self._channel.stop()
                            last_position = None
                            if power == 'hold':
                                self._ask_shutdown()
                            if not active:
                                hardware.set_screen_active(False)
                                if self.camera is not None:
                                    self.camera.request_pause()
                            else:
                                self._resume_pending_capture()
                                if self.camera is not None and (self.mode == "live" or (self.mode == "sensor" and not self._sensor_paused)):
                                    self.camera.request_resume()
                                self._timed_draw(surface, font)
                                hardware.present(surface)
                                hardware.set_screen_active(True)
                            LOGGER.info("Application %s", "active" if active else "paused")
                        if not active:
                            stopping.wait(0.05)
                            continue
                        position = hardware.read_touch()
                        previous = self._pressed
                        if position is not None:
                            if last_position is None:
                                self._pointer_event("lcd", position, True)
                            else:
                                self._pointer_motion("lcd", position)
                            last_position = position
                        elif last_position is not None:
                            self._pointer_event("lcd", last_position, False)
                            last_position = None
                        new_frame = self._poll_camera()
                        self._poll_capture()
                        self._poll_review()
                        self._poll_settings()
                        if self._redraw:
                            self._timed_draw(surface, font)
                            hardware.present(surface)
                            self._redraw = False
                        elif previous != self._pressed or new_frame:
                            regions = []
                            if previous != self._pressed:
                                for index in (previous, self._pressed):
                                    if index is not None:
                                        regions.append(self.buttons[index][1])
                            if new_frame:
                                regions.append(self._plot.AREA.move(self.spectrum_rect.topleft))
                                regions.append(self.camera_slice_rect.inflate(-2, -2))
                            # Clip drawing and transfer only changed pixels.
                            for rect in regions:
                                surface.set_clip(rect)
                                self._timed_draw(surface, font)
                            surface.set_clip(None)
                            hardware.present(surface, regions)
                        elapsed = time.monotonic() - iteration_started
                        METRICS.add('loop_ms', elapsed * 1000)
                        stopping.wait(max(0, 0.02 - elapsed))
        finally:
            self._pointer = self._pressed = None
            pygame.quit()

    def _run_desktop(self):
        try:
            # Avoid initializing audio and other unused subsystems on the Pi.
            pygame.display.init()
            pygame.font.init()
            flags = pygame.FULLSCREEN if self.fullscreen else 0
            surface = pygame.display.set_mode(SCREEN_SIZE, flags)
            pygame.display.set_caption("Spectrometer")
            pygame.mouse.set_visible(not self.fullscreen)
            font = self._font(22)
            self._timed_draw(surface, font)
            pygame.display.flip()
            while True:
                # Block while idle instead of continuously repainting the SPI LCD.
                event = pygame.event.wait(20) if (self.camera is not None or self.review.future is not None
                                                or self.settings_view.future is not None
                                                or self.review.filter_future is not None
                                                or self.mode in ("review", "saved", "sensor", "channel", "rename")) else pygame.event.wait()
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    break
                previous = self._pressed
                self._handle_pointer(event)
                if event.type == pygame.WINDOWFOCUSLOST:
                    self._pointer = self._pressed = None
                    if self._sensor is not None:
                        self._sensor.start = None
                    if self._channel is not None:
                        self._channel.stop()
                new_frame = self._poll_camera()
                self._poll_capture()
                self._poll_review()
                self._poll_settings()
                if self._redraw or new_frame or previous != self._pressed or event.type in (
                    pygame.WINDOWEXPOSED, pygame.WINDOWSHOWN
                ):
                    self._timed_draw(surface, font)
                    pygame.display.flip()
                    self._redraw = False
        finally:
            pygame.quit()
