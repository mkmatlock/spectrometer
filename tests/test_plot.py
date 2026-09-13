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
        self.assertEqual(plot.points[-2:], [[462, 157], [462, 28]])
        self.assertEqual(len(plot.points), 924)
        self.assertEqual(plot.points[-1][0], 462)

    def test_grid_has_five_lines_and_image_matches_plot_span(self):
        from spectrometer.camera import BAR_SIZE
        from spectrometer.ui import SpectrometerUI

        pygame.font.init()
        ui = SpectrometerUI()
        try:
            background = ui._plot._make_background()
            grid_rows = [y for y in range(28, 158)
                         if background.get_at((200, y))[:3] == (48, 64, 80)]
            self.assertEqual(grid_rows, [28, 60, 93, 125, 157])
            image = ui.camera_slice_rect.inflate(-2, -2)
            curve = ui._plot.AREA.move(ui.spectrum_rect.topleft)
            self.assertEqual((image.left, image.right), (curve.left, curve.right))
            self.assertEqual(image.size, BAR_SIZE)
        finally:
            ui.review.close()
            ui.settings_view.close()
            pygame.font.quit()

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
