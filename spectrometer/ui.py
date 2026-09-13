"""Software-rendered touchscreen UI with a live camera spectrum bar."""

import logging
import threading
import pygame


LOGGER = logging.getLogger(__name__)
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
                 on_settings=None, camera=None, review_directory=None):
        self.fullscreen = fullscreen
        self.camera = camera
        self._camera_bar = None
        from .plot import SpectrumPlot

        self._plot = SpectrumPlot()
        from .review import ReviewList

        self.review = ReviewList(review_directory)
        self.mode = "live"
        self._redraw = False
        self._list_start = None
        if on_capture is None and camera is not None:
            on_capture = camera.request_capture
        self.spectrum_rect = pygame.Rect(8, 8, 464, 200)
        self.camera_slice_rect = pygame.Rect(8, 216, 464, 40)
        self.buttons = [
            ("Capture", pygame.Rect(8, 264, 149, 48), on_capture),
            ("Review", pygame.Rect(165, 264, 150, 48), on_review or self._open_review),
            ("Settings", pygame.Rect(323, 264, 149, 48), on_settings),
        ]
        self._pressed = None
        self._pointer = None
        self._live_buttons = self.buttons

    def _open_review(self):
        if self.camera is not None:
            self.camera.pause()
        self._live_view = (self._plot, self._camera_bar)
        self.review.refresh()
        self._review_list()

    def _review_list(self):
        self.mode = "review"
        self.buttons = [("Display", pygame.Rect(8, 264, 228, 48), self.review.display),
                        ("Exit", pygame.Rect(244, 264, 228, 48), self._exit_review)]
        self._redraw = True

    def _exit_review(self):
        if self.review.future is not None:
            self.review.future.cancel()
            self.review.future = None
        self.mode = "live"
        self.buttons = self._live_buttons
        self._plot, self._camera_bar = self._live_view
        if self.camera is not None:
            self.camera.resume()
        self._redraw = True

    def _poll_review(self):
        if self.mode != "review":
            return
        was_loading = self.review.future is not None
        frame = self.review.poll()
        if was_loading and self.review.future is None:
            self._redraw = True
        if frame is not None:
            from .plot import SpectrumPlot
            from .camera import BAR_SIZE

            self._plot = SpectrumPlot()
            self._plot.update(frame.intensity)
            self._camera_bar = pygame.image.frombuffer(frame.bar, BAR_SIZE, "RGB").copy()
            self.mode = "saved"
            self.buttons = [("Back", pygame.Rect(8, 264, 464, 48), self._review_list)]

    def _pointer_motion(self, pointer, position):
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
        if down and self._pointer is None:
            self._pointer = pointer
            self._pressed = self._button_at(position)
            if self.mode == "review" and 34 <= position[1] < 244:
                self._list_start = (position[1], self.review.offset)
        elif not down and self._pointer == pointer:
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
        if self.mode == "review":
            self._draw_review(surface, font)
        if self.mode != "review" and surface.get_clip().colliderect(self.spectrum_rect):
            self._plot.draw(surface, self.spectrum_rect.topleft)
        for rect, label in ((self.camera_slice_rect, "Raw camera slice — placeholder"),):
            if self.mode == "review":
                break
            if not surface.get_clip().colliderect(rect):
                continue
            pygame.draw.rect(surface, PANEL, rect, border_radius=4)
            pygame.draw.rect(surface, BORDER, rect, width=1, border_radius=4)
            text = font.render(label, True, MUTED)
            surface.blit(text, text.get_rect(center=rect.center))
        if self.mode != "review" and self._camera_bar is not None:
            surface.blit(self._camera_bar, self.camera_slice_rect.move(1, 1))
        for i, (label, rect, _) in enumerate(self.buttons):
            if not surface.get_clip().colliderect(rect):
                continue
            color = PRESSED if self._pressed == i else BUTTON
            pygame.draw.rect(surface, color, rect, border_radius=6)
            text = font.render(label, True, TEXT)
            surface.blit(text, text.get_rect(center=rect.center))

    def _draw_review(self, surface, font):
        small = pygame.font.Font(None, 19)
        header = self.review.message or ("Review captures" if self.review.entries else "No captures found")
        surface.blit(small.render(header[:65], True, TEXT), (8, 10))
        for row, path in enumerate(self.review.entries[self.review.offset:self.review.offset + 5]):
            index = row + self.review.offset
            rect = pygame.Rect(8, 34 + row * 42, 450, 40)
            pygame.draw.rect(surface, BUTTON if index == self.review.selected else PANEL, rect)
            surface.blit(font.render(path.stem.removeprefix("spectrum-"), True, TEXT), (16, rect.y + 10))
        if len(self.review.entries) > 5:
            height = max(10, 210 * 5 // len(self.review.entries))
            y = 34 + (210 - height) * self.review.offset // (len(self.review.entries) - 5)
            pygame.draw.rect(surface, MUTED, (464, y, 6, height))

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

    def _poll_camera(self):
        if self.camera is None or self.mode != "live":
            return False
        frame = self.camera.poll()
        if frame is None:
            return False
        from .camera import BAR_SIZE

        self._camera_bar = pygame.image.frombuffer(frame.bar, BAR_SIZE, "RGB").copy()
        self._plot.update(frame.intensity)
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
                    font = pygame.font.Font(None, 22)
                    self.draw(surface, font)
                    hardware.present(surface)
                    last_position = None
                    active = True
                    while not stopping.is_set():
                        if hardware.power_pressed():
                            active = not active
                            self._pointer = self._pressed = None
                            self._list_start = None
                            last_position = None
                            if not active:
                                hardware.set_screen_active(False)
                                if self.camera is not None:
                                    self.camera.pause()
                            else:
                                if self.camera is not None and self.mode == "live":
                                    self.camera.resume()
                                self.draw(surface, font)
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
                        self._poll_review()
                        if self._redraw:
                            self.draw(surface, font)
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
                                self.draw(surface, font)
                            surface.set_clip(None)
                            hardware.present(surface, regions)
                        stopping.wait(0.02)
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
            font = pygame.font.Font(None, 22)
            self.draw(surface, font)
            pygame.display.flip()
            while True:
                # Block while idle instead of continuously repainting the SPI LCD.
                event = pygame.event.wait(20) if self.camera is not None or self.review.future is not None else pygame.event.wait()
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    break
                previous = self._pressed
                self._handle_pointer(event)
                if event.type == pygame.WINDOWFOCUSLOST:
                    self._pointer = self._pressed = None
                new_frame = self._poll_camera()
                self._poll_review()
                if self._redraw or new_frame or previous != self._pressed or event.type in (
                    pygame.WINDOWEXPOSED, pygame.WINDOWSHOWN
                ):
                    self.draw(surface, font)
                    pygame.display.flip()
                    self._redraw = False
        finally:
            pygame.quit()
