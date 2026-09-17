from pathlib import Path
import pickle
import tempfile
import unittest

import numpy as np
import pygame

from spectrometer.blackbody import _shape, fit_blackbody
from spectrometer.scale import WavelengthScale
from spectrometer.ui import SpectrometerUI
from tests.spectrum_fixtures import spectrum_record


class BlackBodyTests(unittest.TestCase):
    def test_recovers_temperature_and_scaled_curve(self):
        labels = {0: 400.0, 3499: 900.0}
        scale = WavelengthScale(labels)
        wavelengths = scale.wavelength(np.arange(3500)) * 1e-9
        expected = 4200.0
        values = 1200.0 + 50000.0 * _shape(wavelengths, expected)

        fit = fit_blackbody(values, (0, 0, 3500, 250), scale)

        self.assertAlmostEqual(fit.temperature, expected, delta=2.0)
        np.testing.assert_allclose(fit.intensity, values, rtol=2e-4)

    def test_review_toggle_draws_fit_and_temperature(self):
        pygame.font.init()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'spectrum-2026-09-15-12-00-00.pkl'
            labels = {0: 400.0, 3499: 900.0}
            scale = WavelengthScale(labels)
            wavelengths = scale.wavelength(np.arange(3500)) * 1e-9
            values = np.rint(1000 + 40000 * _shape(wavelengths, 5000)).astype(np.int32)
            record = spectrum_record(spectrum_intensity=values)
            record['instrument_settings']['calibration_settings']['scale'] = labels
            path.write_bytes(pickle.dumps(record))
            ui = SpectrometerUI(review_directory=directory)
            try:
                ui._open_review()
                ui.review.selected = 0
                ui.review.display()
                ui.review.future.result(timeout=5)
                ui._poll_review()
                ui._ask_filter()
                ui._choose_filter('Black Body')
                self.assertTrue(ui._blackbody)
                self.assertEqual(ui._blackbody_label, 'Fitting...')
                ui.review.blackbody_future.result(timeout=5)
                ui._poll_review()
                self.assertIn('5,000 K', ui._blackbody_label)
                self.assertTrue(ui._plot.fit_points)

                ui._choose_filter('Black Body')
                self.assertFalse(ui._blackbody)
                self.assertIsNone(ui._plot.fit_points)
            finally:
                ui.review.close()
                ui.settings_view.close()
        pygame.font.quit()

    def test_requires_wavelength_calibration(self):
        ui = SpectrometerUI()
        try:
            ui.mode = 'saved'
            ui._spectrum_intensity = np.zeros(3500)
            ui._ask_filter()
            ui._choose_filter('Black Body')
            self.assertFalse(ui._blackbody)
            self.assertIn('wavelength calibration', ui._mode_message)
        finally:
            ui.review.close()
            ui.settings_view.close()
