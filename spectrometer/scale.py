"""Find local maxima once per recorded spectrum for touch selection."""

import numpy as np


def peak_indices(intensity):
    values = np.asarray(intensity)
    # Treat a flat-topped peak as one peak at the centre of its plateau.
    starts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1]
    ends = np.r_[starts[1:] - 1, len(values) - 1]
    interior = (starts > 0) & (ends < len(values) - 1)
    starts, ends = starts[interior], ends[interior]
    peaks = (values[starts] > values[starts - 1]) & (values[ends] > values[ends + 1])
    return ((starts[peaks] + ends[peaks]) // 2).astype(np.int32)


class PeakSelection:
    def __init__(self, intensity, area, maximum, pixel_origin=0):
        self.indices = peak_indices(intensity)
        self.positions = np.empty((len(self.indices), 2), dtype=float)
        self.positions[:, 0] = area.right - 1 - self.indices * (area.width - 1) / (len(intensity) - 1)
        self.positions[:, 1] = area.bottom - 1 - np.clip(np.asarray(intensity)[self.indices], 0, maximum) * ((area.height - 1) / maximum)
        self.indices = self.indices + pixel_origin
        self.selected = None

    def select(self, position):
        if not len(self.indices):
            self.selected = None
            return None
        self.selected = int(np.argmin(np.sum((self.positions - position) ** 2, axis=1)))
        return int(self.indices[self.selected])

    @property
    def marker(self):
        if self.selected is None:
            return None
        return tuple(np.rint(self.positions[self.selected]).astype(int))
