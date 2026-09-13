import unittest

import numpy as np
import pygame

from spectrometer.plot import SpectrumPlot


class PlotTests(unittest.TestCase):
    def test_narrow_peak_and_dark_trough_survive_screen_reduction(self):
        plot = SpectrumPlot()
        values = np.full(3500, 10000, dtype=np.int32)
        values[3] = 63750
        values[4] = 0
        plot.update(values)
        self.assertEqual(plot.points[:2], [[52, 157], [52, 28]])
        self.assertEqual(len(plot.points), 800)
        self.assertEqual(plot.points[-1][0], 451)

    def test_axes_cached_and_trace_changes_on_update(self):
        pygame.font.init()
        try:
            plot = SpectrumPlot()
            surface = pygame.Surface(plot.SIZE)
            plot.update(np.zeros(3500, dtype=np.int32))
            plot.draw(surface, (0, 0))
            background = plot._background
            self.assertEqual(surface.get_at((100, 157))[:3], plot.TRACE)
            plot.update(np.full(3500, 63750, dtype=np.int32))
            plot.draw(surface, (0, 0))
            self.assertIs(plot._background, background)
            self.assertEqual(surface.get_at((100, 28))[:3], plot.TRACE)
            self.assertNotEqual(surface.get_at((100, 157))[:3], plot.TRACE)
        finally:
            pygame.font.quit()
