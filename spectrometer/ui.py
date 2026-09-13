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
                 on_settings=None, camera=None):
        self.fullscreen = fullscreen
        self.camera = camera
        self._camera_bar = None
        from .plot import SpectrumPlot

        self._plot = SpectrumPlot()
        self.spectrum_rect = pygame.Rect(8, 8, 464, 200)
        self.camera_slice_rect = pygame.Rect(8, 216, 464, 40)
        self.buttons = [
            ("Capture", pygame.Rect(8, 264, 149, 48), on_capture),
            ("Review", pygame.Rect(165, 264, 150, 48), on_review),
            ("Settings", pygame.Rect(323, 264, 149, 48), on_settings),
        ]
        self._pressed = None
        self._pointer = None

    def _button_at(self, position):
        return next((i for i, (_, rect, _) in enumerate(self.buttons)
                     if rect.collidepoint(position)), None)

    def _handle_pointer(self, event):
        """Activate on release inside the original button; ignore extra fingers."""
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
        elif not down and self._pointer == pointer:
            selected = self._pressed
            self._pointer = self._pressed = None
            if selected is not None and self._button_at(position) == selected:
                label, _, callback = self.buttons[selected]
                if callback is not None:
                    callback()
                else:
                    LOGGER.info("%s is not implemented yet", label)

    def draw(self, surface, font):
        """Draw cached spectrum axes, trace, camera slice, and touch controls."""
        surface.fill(BACKGROUND)
        if surface.get_clip().colliderect(self.spectrum_rect):
            self._plot.draw(surface, self.spectrum_rect.topleft)
        for rect, label in ((self.camera_slice_rect, "Raw camera slice — placeholder"),):
            if not surface.get_clip().colliderect(rect):
                continue
            pygame.draw.rect(surface, PANEL, rect, border_radius=4)
            pygame.draw.rect(surface, BORDER, rect, width=1, border_radius=4)
            text = font.render(label, True, MUTED)
            surface.blit(text, text.get_rect(center=rect.center))
        if self._camera_bar is not None:
            surface.blit(self._camera_bar, self.camera_slice_rect.move(1, 1))
        for i, (label, rect, _) in enumerate(self.buttons):
            if not surface.get_clip().colliderect(rect):
                continue
            color = PRESSED if self._pressed == i else BUTTON
            pygame.draw.rect(surface, color, rect, border_radius=6)
            text = font.render(label, True, TEXT)
            surface.blit(text, text.get_rect(center=rect.center))

    def run(self):
        """Use the directly connected LCD unless a desktop preview is requested."""
        from contextlib import nullcontext

        with (self.camera if self.camera is not None else nullcontext()):
            if self.fullscreen:
                self._run_lcd()
            else:
                self._run_desktop()

    def _poll_camera(self):
        if self.camera is None:
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
                    while not stopping.is_set():
                        position = hardware.read_touch()
                        previous = self._pressed
                        if position is not None:
                            if last_position is None:
                                self._pointer_event("lcd", position, True)
                            last_position = position
                        elif last_position is not None:
                            self._pointer_event("lcd", last_position, False)
                            last_position = None
                        new_frame = self._poll_camera()
                        if previous != self._pressed or new_frame:
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
                event = pygame.event.wait(20) if self.camera is not None else pygame.event.wait()
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                ):
                    break
                previous = self._pressed
                self._handle_pointer(event)
                if event.type == pygame.WINDOWFOCUSLOST:
                    self._pointer = self._pressed = None
                new_frame = self._poll_camera()
                if new_frame or previous != self._pressed or event.type in (
                    pygame.WINDOWEXPOSED, pygame.WINDOWSHOWN
                ):
                    self.draw(surface, font)
                    pygame.display.flip()
        finally:
            pygame.quit()
