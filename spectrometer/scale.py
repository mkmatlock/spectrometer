"""Find local maxima once per recorded spectrum for touch selection."""

import numpy as np


class WavelengthScale:
    """Piecewise linear calibration, including the two outer extrapolations."""

    def __init__(self, labels):
        points = sorted(labels.items())
        self.pixels = np.array([p for p, _ in points], dtype=float)
        self.values = np.array([v for _, v in points], dtype=float)
        self.slopes = np.diff(self.values) / np.diff(self.pixels)

    def wavelength(self, pixel):
        segment = np.clip(np.searchsorted(self.pixels, pixel, side='right') - 1,
                          0, len(self.slopes) - 1)
        return self.values[segment] + (pixel - self.pixels[segment]) * self.slopes[segment]

    def ticks(self, start, end):
        # Work segment by segment: an inverse interpolation would incorrectly
        # assume wavelength labels are monotonic in sensor pixel coordinates.
        knots = [start, *self.pixels[(self.pixels > start) & (self.pixels < end)], end]
        ticks = {}
        for left, right in zip(knots, knots[1:]):
            first, last = float(self.wavelength(left)), float(self.wavelength(right))
            if first == last:
                continue
            low, high = sorted((first, last))
            for value in range(int(np.ceil(low / 50)) * 50, int(np.floor(high / 50)) * 50 + 1, 50):
                pixel = left + (value - first) * (right - left) / (last - first)
                ticks[round(pixel, 8)] = (pixel, value)
        return sorted(ticks.values())


def peak_indices(intensity, absorption=False):
    values = np.asarray(intensity)
    # Treat a flat-topped peak as one peak at the centre of its plateau.
    starts = np.r_[0, np.flatnonzero(values[1:] != values[:-1]) + 1]
    ends = np.r_[starts[1:] - 1, len(values) - 1]
    interior = (starts > 0) & (ends < len(values) - 1)
    starts, ends = starts[interior], ends[interior]
    compare = np.less if absorption else np.greater
    peaks = compare(values[starts], values[starts - 1]) & compare(values[ends], values[ends + 1])
    return ((starts[peaks] + ends[peaks]) // 2).astype(np.int32)


class PeakSelection:
    def __init__(self, intensity, area, maximum, pixel_origin=0, absorption=False):
        self.indices = peak_indices(intensity, absorption)
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
