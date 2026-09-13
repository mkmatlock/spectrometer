"""Cached axes and a screen-resolution min/max trace; no plotting framework."""

import numpy as np
import pygame

from .camera import SPECTRUM_ROI


class SpectrumPlot:
    SIZE = (464, 200)
    AREA = pygame.Rect(52, 28, 400, 130)
    TRACE = (103, 219, 185)

    def __init__(self):
        x0, y0, x1, y1 = SPECTRUM_ROI
        self.maximum = (y1 - y0) * 255
        self.edges = np.linspace(0, x1 - x0, self.AREA.width + 1, dtype=np.int32)
        self._xs = np.repeat(np.arange(self.AREA.left, self.AREA.right), 2)
        self._background = None
        self.points = None

    def update(self, intensity):
        values = np.asarray(intensity)
        if values.shape != (SPECTRUM_ROI[2] - SPECTRUM_ROI[0],):
            raise ValueError("Spectrum must contain one total per ROI column")
        # Preserve both narrow emission peaks and absorption troughs. Decimation
        # by stride or averaging would hide features smaller than a screen pixel.
        low = np.minimum.reduceat(values, self.edges[:-1])
        high = np.maximum.reduceat(values, self.edges[:-1])
        envelope = np.column_stack((low, high)).reshape(-1)
        ys = self.AREA.bottom - 1 - np.rint(
            np.clip(envelope, 0, self.maximum) * ((self.AREA.height - 1) / self.maximum)
        ).astype(np.int32)
        self.points = np.column_stack((self._xs, ys)).tolist()

    def _make_background(self):
        surface = pygame.Surface(self.SIZE)
        surface.fill("#1b2632")
        pygame.draw.rect(surface, "#405367", surface.get_rect(), 1, border_radius=4)
        font = pygame.font.Font(None, 18)
        surface.blit(font.render("Total intensity", True, "#a9bacb"), (8, 5))
        for value in (0, self.maximum // 2, self.maximum):
            y = self.AREA.bottom - 1 - round(value * (self.AREA.height - 1) / self.maximum)
            pygame.draw.line(surface, "#304050", (self.AREA.left, y), (self.AREA.right - 1, y))
            label = font.render(str(value), True, "#a9bacb")
            surface.blit(label, label.get_rect(midright=(self.AREA.left - 5, y)))
        x0, _, x1, _ = SPECTRUM_ROI
        for value in (x0, 1000, 2000, x1 - 1):
            x = self.AREA.left + round((value - x0) * (self.AREA.width - 1) / (x1 - x0 - 1))
            label = font.render(str(value), True, "#a9bacb")
            rect = label.get_rect(midtop=(x, self.AREA.bottom + 4))
            rect.clamp_ip(surface.get_rect())
            surface.blit(label, rect)
        label = font.render("Sensor X (pixels)", True, "#a9bacb")
        surface.blit(label, label.get_rect(midbottom=(self.AREA.centerx, self.SIZE[1] - 3)))
        return surface

    def draw(self, surface, position):
        if self._background is None:
            self._background = self._make_background()
        surface.blit(self._background, position)
        if self.points is not None:
            # Draw on a subsurface to keep point coordinates local and respect
            # the caller's dirty rectangle without transforming 800 points.
            patch = surface.subsurface(pygame.Rect(position, self.SIZE))
            patch.set_clip(surface.get_clip().move(-position[0], -position[1]))
            pygame.draw.lines(patch, self.TRACE, False, self.points)
