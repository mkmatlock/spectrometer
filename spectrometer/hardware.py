"""Bridge the supplied SPI LCD and I2C touchscreen drivers to the UI."""

from contextlib import ExitStack
import logging
import queue
import threading
import time

from .performance import PerformanceMetrics


LOGGER = logging.getLogger(__name__)
METRICS = PerformanceMetrics('lcd')


class LCDBackend:
    """Own the hardware resources; importing this module needs no Pi libraries."""

    def __init__(self):
        self._last_touch = None
        self._power_down = False
        self._power_started = None
        self._power_held = False
        self._present_error = None

    def __enter__(self):
        from .st7796 import st7796
        from .ft6336u import ft6336u

        self._resources = ExitStack()
        try:
            self.display = st7796()
            self._resources.callback(self.display.close)
            self._present_queue = queue.Queue(maxsize=1)
            self._present_error = None
            self._present_stop = object()
            self._present_thread = threading.Thread(target=self._present_worker,
                                                    name='lcd-present', daemon=True)
            self._present_thread.start()
            self._resources.callback(self._stop_presenter)
            self.touch = ft6336u()
            self._resources.callback(self.touch.close)
            from gpiozero import Button

            # Momentary switch between BCM21 (physical pin 40) and ground.
            self.power_button = Button(21, pull_up=True, bounce_time=0.05)
            self._resources.callback(self.power_button.close)
        except BaseException:
            self._resources.close()
            raise
        return self

    def __exit__(self, *exc):
        return self._resources.__exit__(*exc)

    def power_event(self):
        """Short press on release, or one hold event after three seconds."""
        down = self.power_button.is_pressed
        now = time.monotonic()
        event = None
        if down and not self._power_down:
            self._power_started = now
            self._power_held = False
        if self._power_down or down:
            if not self._power_held and now - self._power_started >= 3:
                event = 'hold'
                self._power_held = True
            elif not down and not self._power_held:
                event = 'short'
        self._power_down = down
        return event

    def set_screen_active(self, active):
        self.display.bl_DutyCycle(100 if active else 0)

    @staticmethod
    def _merge_regions(first, second, full_rect):
        if first is None or second is None:
            return None
        result = []
        for rect in (*first, *second):
            clipped = rect.clip(full_rect)
            if clipped.width and clipped.height and clipped not in result:
                result.append(clipped)
        return result

    def _prepare(self, surface, regions):
        import pygame
        rects = [surface.get_rect()] if regions is None else list(regions)
        patches = []
        for rect in rects:
            patch = surface.subsurface(rect)
            width, height = patch.get_size()
            rgb = pygame.surfarray.pixels3d(patch).swapaxes(0, 1)
            try:
                pixels = self.display.prepare_array(width, height, rgb, mirror=True)
            finally:
                del rgb
            patches.append((rect.x, rect.y, width, height, pixels, regions is None))
        return patches

    def _present_worker(self):
        while True:
            task = self._present_queue.get()
            try:
                if task is self._present_stop:
                    return
                _, patches = task
                for patch in patches:
                    self.display.write_prepared(*patch)
            except Exception as exc:
                self._present_error = exc
                LOGGER.exception('LCD presentation failed')
            finally:
                self._present_queue.task_done()

    def _stop_presenter(self):
        self._present_queue.join()
        self._present_queue.put(self._present_stop)
        self._present_thread.join()

    def _queue_present(self, surface, regions):
        wanted = None if regions is None else list(regions)
        while True:
            try:
                old_regions, _ = self._present_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._present_queue.task_done()
                wanted = self._merge_regions(old_regions, wanted, surface.get_rect())
        patches = self._prepare(surface, wanted)
        self._present_queue.put_nowait((wanted, patches))

    def present(self, surface, regions=None):
        started = time.monotonic()
        if self._present_error is not None:
            raise RuntimeError('LCD presentation failed') from self._present_error
        pixels = sum(rect.width * rect.height for rect in
                     ([surface.get_rect()] if regions is None else regions))
        self._queue_present(surface, regions)
        elapsed_ms = (time.monotonic() - started) * 1000
        METRICS.add('transfer_ms', elapsed_ms)
        METRICS.add('pixels', pixels)

    def read_touch(self):
        started = time.monotonic()
        self.touch.read_touch_data()
        count, coordinates = self.touch.get_touch_xy()
        METRICS.add('touch_read_ms', (time.monotonic() - started) * 1000)
        if not count:
            if self._last_touch is not None:
                LOGGER.debug("Touch released")
            self._last_touch = None
            return None
        # Undo the driver's native X mirror before using it as landscape Y.
        # Physical button samples confirm UI = (479 - raw_y, raw_x).
        point = coordinates[0]
        position = (479 - point["y"], 319 - point["x"])
        sample = (count, point["x"], point["y"])
        if sample != self._last_touch:
            LOGGER.debug("Touch count=%s raw=(%s, %s) UI=%s", count,
                         319 - point["x"], point["y"], position)
        self._last_touch = sample
        # Do not turn an invalid sample into a press on a screen edge.
        if not (0 <= position[0] < 480 and 0 <= position[1] < 320):
            return None
        return position
