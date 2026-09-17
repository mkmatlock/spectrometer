import unittest
from copy import deepcopy

import numpy as np
import pygame

from spectrometer.camera import SpectrumFrame
from spectrometer.plot import SpectrumPlot
from spectrometer.scale import WavelengthScale
from spectrometer.ui import SpectrometerUI
from spectrometer.config import DEFAULTS


class WavelengthTests(unittest.TestCase):
    def test_interpolation_and_outer_extrapolation(self):
        scale = WavelengthScale({300: 400, 100: 700, 200: 600})
        for pixel, expected in [(0, 800), (100, 700), (150, 650),
                                (200, 600), (250, 500), (300, 400), (350, 300)]:
            self.assertEqual(scale.wavelength(pixel), expected)

    def test_ticks_follow_each_segment_and_extrapolate_without_duplicate_knots(self):
        scale = WavelengthScale({100: 700, 200: 600, 300: 400})
        ticks = scale.ticks(50, 350)
        self.assertEqual([value for _, value in ticks], list(range(750, 299, -50)))
        self.assertEqual([pixel for pixel, _ in ticks],
                         [50, 100, 150, 200, 225, 250, 275, 300, 325, 350])
        self.assertEqual(WavelengthScale({0: 400, 100: 500, 200: 400}).ticks(0, 200),
                         [(0, 400), (50, 450), (100, 500), (150, 450), (200, 400)])

    def test_axis_cache_and_pixel_fallback(self):
        pygame.font.init()
        self.addCleanup(pygame.font.quit)
        plot = SpectrumPlot((100, 0, 1100, 100))
        plot.set_calibration({100: 700})
        self.assertIsNone(plot.scale)
        self.assertEqual(plot.axis_ticks(), [(500, 500), (1000, 1000)])
        plot.set_calibration({100: 700, 1100: 400})
        plot.draw(pygame.Surface(plot.SIZE), (0, 0))
        background = plot._background
        self.assertEqual([v for _, v in plot.axis_ticks()], [700, 650, 600, 550, 500, 450])
        self.assertFalse(plot.set_calibration({1100: 400, 100: 700}))
        self.assertIs(plot._background, background)
        self.assertTrue(plot.set_calibration({100: 700}))
        self.assertIsNone(plot._background)
        self.assertIsNone(plot.scale)

    def test_review_nm_scale_pixels_and_live_refresh_after_label_changes(self):
        ui = SpectrometerUI(calibration_settings=dict(deepcopy(DEFAULTS['calibration']),
                                                     scale={1000: 700, 2000: 500}))
        self.addCleanup(ui.review.close)
        self.addCleanup(ui.settings_view.close)
        values = np.zeros(3500, np.int32)
        values[1500] = 50000
        frame = SpectrumFrame(bytes(462 * 38 * 3), values,
                              calibration={'scale': {1000: 700, 2000: 500}})
        ui.mode = 'saved'
        ui._update_plot(frame)
        ui._reset_peaks(values)
        ui._select_peak(ui._peaks.positions[0])
        self.assertEqual(ui._review_peak_label, '600.0 nm')
        self.assertIsNotNone(ui._peaks.marker)
        self.assertFalse(ui._peak_dialog)
        ui._scale_active = True
        ui._update_plot(frame)
        self.assertIsNone(ui._plot.scale)
        ui._select_peak(ui._peaks.positions[0])
        self.assertEqual(ui._peak_message, 'Pixel 1500')
        self.assertTrue(ui._peak_dialog)
        ui._update_scale(2000, None)
        ui._scale_active = False
        ui._exit_settings()
        self.assertIsNone(ui._plot.scale)
        ui._update_scale(2000, 400)
        ui._exit_settings()
        self.assertEqual(ui._plot.scale.wavelength(1500), 550)
