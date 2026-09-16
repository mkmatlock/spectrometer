"""Three-channel calibration graph and dual-thumb pixel-range controls."""

import numpy as np
import pygame


COLORS = {'Red': (244, 92, 92), 'Green': (92, 220, 130), 'Blue': (92, 150, 255)}


class ChannelCalibration:
    GRAPH = pygame.Rect(28, 34, 436, 112)
    TRACK = pygame.Rect(48, 0, 410, 4)
    ROWS = {'Red': 168, 'Green': 200, 'Blue': 232}

    def __init__(self, frames, ranges=None):
        self.frames = frames
        self.roi = tuple(next(iter(frames.values())).roi)
        self.maximum = (self.roi[3] - self.roi[1]) * 255
        self.ranges = self.full_ranges()
        for name, extent in (ranges or {}).items():
            if name in self.ranges:
                self.ranges[name] = self._clamp(extent)
        self.dragging = None
        self.message = ''
        self.curves = {name: self._curve(frame.intensity) for name, frame in frames.items()}

    def full_ranges(self):
        return {name: (self.roi[0], self.roi[2] - 1) for name in COLORS}

    def reset(self):
        self.ranges = self.full_ranges()
        self.dragging = None
        self.message = ''

    def _clamp(self, extent):
        low = min(self.roi[2] - 1, max(self.roi[0], int(extent[0])))
        high = min(self.roi[2] - 1, max(self.roi[0], int(extent[1])))
        return min(low, high), max(low, high)

    def _curve(self, intensity):
        values = np.asarray(intensity)
        edges = np.linspace(0, len(values), self.GRAPH.width + 1, dtype=np.int32)
        reduced = np.maximum.reduceat(values, edges[:-1])[::-1]
        ys = self.GRAPH.bottom - 1 - np.rint(
            np.clip(reduced, 0, self.maximum) * (self.GRAPH.height - 1) / self.maximum
        ).astype(np.int32)
        return np.column_stack((np.arange(self.GRAPH.left, self.GRAPH.right), ys)).tolist()

    def _thumb_x(self, pixel):
        x0, _, x1, _ = self.roi
        return self.TRACK.right - 1 - round((pixel - x0) * (self.TRACK.width - 1) / (x1 - x0 - 1))

    def _pixel(self, x):
        x0, _, x1, _ = self.roi
        fraction = min(1.0, max(0.0, (self.TRACK.right - 1 - x) / (self.TRACK.width - 1)))
        return x0 + round(fraction * (x1 - x0 - 1))

    def start(self, position):
        for name, y in self.ROWS.items():
            if abs(position[1] - y) <= 14 and self.TRACK.left - 12 <= position[0] <= self.TRACK.right + 12:
                extent = self.ranges[name]
                endpoint = min((0, 1), key=lambda index: abs(position[0] - self._thumb_x(extent[index])))
                self.dragging = (name, endpoint)
                self.drag(position)
                return True
        return False

    def drag(self, position):
        if self.dragging is None:
            return
        name, endpoint = self.dragging
        low, high = self.ranges[name]
        pixel = self._pixel(position[0])
        self.ranges[name] = ((min(pixel, high), high) if endpoint == 0
                             else (low, max(pixel, low)))
        self.message = ''

    def stop(self):
        self.dragging = None

    def draw(self, surface, font):
        small = pygame.font.Font(None, 18)
        surface.blit(small.render(self.message or 'Channel calibration', True, '#edf3f8'), (8, 8))
        pygame.draw.rect(surface, '#1b2632', self.GRAPH)
        for fraction in (0, .25, .5, .75, 1):
            y = self.GRAPH.bottom - 1 - round(fraction * (self.GRAPH.height - 1))
            pygame.draw.line(surface, '#304050', (self.GRAPH.left, y), (self.GRAPH.right - 1, y))
        for name in ('Red', 'Green', 'Blue'):
            pygame.draw.lines(surface, COLORS[name], False, self.curves[name], 1)
        for name, y in self.ROWS.items():
            color = COLORS[name]
            surface.blit(font.render(name[0], True, color), (16, y - 10))
            track = self.TRACK.copy()
            track.centery = y
            pygame.draw.rect(surface, '#405367', track, border_radius=2)
            low, high = self.ranges[name]
            left, right = sorted((self._thumb_x(low), self._thumb_x(high)))
            pygame.draw.line(surface, color, (left, y), (right, y), 4)
            for pixel in (low, high):
                x = self._thumb_x(pixel)
                pygame.draw.circle(surface, color, (x, y), 8)
                pygame.draw.circle(surface, '#edf3f8', (x, y), 8, 1)
            label = '%d–%d' % (low, high)
            text = small.render(label, True, '#a9bacb')
            surface.blit(text, text.get_rect(midbottom=(self.TRACK.centerx, y - 7)))
