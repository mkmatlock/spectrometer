"""Cached axes and a screen-resolution min/max trace; no plotting framework."""

import numpy as np
import pygame

from .camera import SPECTRUM_ROI


class SpectrumPlot:
    SIZE = (464, 200)
    AREA = pygame.Rect(1, 28, 462, 130)
    TRACE = (103, 219, 185)
    FIT_TRACE = (255, 209, 102)

    def __init__(self, roi=SPECTRUM_ROI, maximum=None):
        self.roi = tuple(roi)
        x0, y0, x1, y1 = self.roi
        self.maximum = maximum or (y1 - y0) * 255 * 3
        self.edges = np.linspace(0, x1 - x0, self.AREA.width + 1, dtype=np.int32)
        self._xs = np.repeat(np.arange(self.AREA.left, self.AREA.right), 2)
        self._background = None
        self.points = None
        self.fit_points = None
        self._labels = ()
        self.scale = None

    def set_calibration(self, labels):
        points = tuple(sorted(labels.items()))
        if points == self._labels:
            return False
        from .scale import WavelengthScale
        self._labels = points
        self.scale = WavelengthScale(labels) if len(points) >= 2 else None
        self._background = None
        return True

    def axis_ticks(self):
        x0, _, x1, _ = self.roi
        if self.scale is not None:
            return self.scale.ticks(x0, x1 - 1)
        return [(value, value) for value in range(int(np.ceil(x0 / 500)) * 500, x1, 500)]

    def update(self, intensity):
        values = np.asarray(intensity)
        if values.shape != (self.roi[2] - self.roi[0],):
            raise ValueError("Spectrum must contain one total per ROI column")
        # Preserve both narrow emission peaks and absorption troughs. Decimation
        # by stride or averaging would hide features smaller than a screen pixel.
        low = np.minimum.reduceat(values, self.edges[:-1])
        high = np.maximum.reduceat(values, self.edges[:-1])
        # Reverse screen bins only; sensor indices and stored spectra stay unchanged.
        envelope = np.column_stack((low[::-1], high[::-1])).reshape(-1)
        ys = self.AREA.bottom - 1 - np.rint(
            np.clip(envelope, 0, self.maximum) * ((self.AREA.height - 1) / self.maximum)
        ).astype(np.int32)
        self.points = np.column_stack((self._xs, ys)).tolist()

    def set_fit(self, intensity=None):
        """Set or clear a fitted curve drawn over the measured spectrum."""
        if intensity is None:
            self.fit_points = None
            return
        values = np.asarray(intensity, dtype=float)
        if values.shape != (self.roi[2] - self.roi[0],):
            raise ValueError("Fit must contain one value per ROI column")
        centres = (self.edges[:-1] + self.edges[1:] - 1) // 2
        sampled = values[centres][::-1]
        valid = np.isfinite(sampled)
        sampled = np.where(valid, sampled, 0.0)
        ys = self.AREA.bottom - 1 - np.rint(
            np.clip(sampled, 0, self.maximum) * ((self.AREA.height - 1) / self.maximum)
        ).astype(np.int32)
        points = np.column_stack((np.arange(self.AREA.left, self.AREA.right), ys))
        self.fit_points = [points[start:end].tolist() for start, end in _runs(valid)
                           if end - start >= 2]

    def _make_background(self):
        surface = pygame.Surface(self.SIZE)
        surface.fill("#1b2632")
        pygame.draw.rect(surface, "#405367", surface.get_rect(), 1, border_radius=4)
        font = pygame.font.Font(None, 18)
        surface.blit(font.render("Intensity", True, "#a9bacb"), (8, 5))
        for fraction in (0, 0.25, 0.5, 0.75, 1):
            y = self.AREA.bottom - 1 - round(fraction * (self.AREA.height - 1))
            pygame.draw.line(surface, "#304050", (self.AREA.left, y), (self.AREA.right - 1, y))
        x0, _, x1, _ = self.roi
        for pixel, value in self.axis_ticks():
            x = self.AREA.right - 1 - round((pixel - x0) * (self.AREA.width - 1) / (x1 - x0 - 1))
            pygame.draw.line(surface, "#a9bacb", (x, self.AREA.bottom - 1), (x, self.AREA.bottom + 2))
            label = font.render(str(value), True, "#a9bacb")
            rect = label.get_rect(midtop=(x, self.AREA.bottom + 4))
            rect.clamp_ip(surface.get_rect())
            surface.blit(label, rect)
        label = font.render("Wavelength (nm)" if self.scale is not None else "Pixel", True, "#a9bacb")
        surface.blit(label, label.get_rect(midbottom=(self.AREA.centerx, self.SIZE[1] - 3)))
        return surface

    def draw(self, surface, position):
        if self._background is None:
            self._background = self._make_background()
        surface.blit(self._background, position)
        if self.points is not None:
            # Draw on a subsurface to keep point coordinates local and respect
            # the caller's dirty rectangle without transforming the trace points.
            patch = surface.subsurface(pygame.Rect(position, self.SIZE))
            patch.set_clip(surface.get_clip().move(-position[0], -position[1]))
            pygame.draw.lines(patch, self.TRACE, False, self.points)
        if self.fit_points:
            patch = surface.subsurface(pygame.Rect(position, self.SIZE))
            patch.set_clip(surface.get_clip().move(-position[0], -position[1]))
            for points in self.fit_points:
                pygame.draw.lines(patch, self.FIT_TRACE, False, points, 2)


def _runs(mask):
    padded = np.r_[False, mask, False].astype(np.int8)
    changes = np.flatnonzero(np.diff(padded))
    return zip(changes[::2], changes[1::2])
