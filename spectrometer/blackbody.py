"""Lightweight single-temperature black-body fitting for recorded spectra."""

from dataclasses import dataclass

import numpy as np


SECOND_RADIATION_CONSTANT = 1.438776877e-2  # metre kelvin
MIN_TEMPERATURE = 100.0
MAX_TEMPERATURE = 100000.0


@dataclass(frozen=True)
class BlackBodyFit:
    temperature: float
    intensity: np.ndarray


def _shape(wavelength_metres, temperature):
    """Return a normalized Planck wavelength-law curve without large powers."""
    exponent = SECOND_RADIATION_CONSTANT / (wavelength_metres * temperature)
    log_denominator = np.empty_like(exponent)
    ordinary = exponent < 50.0
    log_denominator[ordinary] = np.log(np.expm1(exponent[ordinary]))
    # log(exp(x) - 1), written this way, stays finite in the Wien tail.
    log_denominator[~ordinary] = exponent[~ordinary] + np.log1p(
        -np.exp(-exponent[~ordinary]))
    log_curve = -5.0 * np.log(wavelength_metres) - log_denominator
    log_curve -= np.max(log_curve)
    return np.exp(log_curve)


def _linear_model(shape, values):
    """Fit the unknown detector gain and background for one temperature."""
    centred_shape = shape - shape.mean()
    centred_values = values - values.mean()
    denominator = np.dot(centred_shape, centred_shape)
    gain = max(0.0, np.dot(centred_shape, centred_values) / denominator) if denominator else 0.0
    offset = values.mean() - gain * shape.mean()
    model = offset + gain * shape
    return float(np.dot(values - model, values - model)), gain, offset


def fit_blackbody(intensity, roi, scale):
    """Fit temperature plus linear gain/background and return display intensities.

    Wavelength calibration is supplied by ``WavelengthScale``. The nonlinear
    search is one-dimensional because gain and background have closed-form
    least-squares solutions for each trial temperature.
    """
    values = np.asarray(intensity, dtype=np.float64)
    pixels = np.arange(roi[0], roi[2], dtype=np.float64)
    wavelengths = np.asarray(scale.wavelength(pixels), dtype=np.float64) * 1e-9
    valid = np.isfinite(values) & np.isfinite(wavelengths) & (wavelengths > 0)
    if np.count_nonzero(valid) < 8 or np.ptp(wavelengths[valid]) == 0:
        raise ValueError('Wavelength calibration does not cover the spectrum')

    fit_wavelengths = wavelengths[valid]
    fit_values = values[valid]

    def score(log_temperature):
        temperature = float(np.exp(log_temperature))
        curve = _shape(fit_wavelengths, temperature)
        return _linear_model(curve, fit_values)[0]

    low, high = np.log(MIN_TEMPERATURE), np.log(MAX_TEMPERATURE)
    trials = np.linspace(low, high, 96)
    scores = np.array([score(value) for value in trials])
    best = int(np.argmin(scores))
    left = trials[max(0, best - 1)]
    right = trials[min(len(trials) - 1, best + 1)]

    # Golden-section refinement in log-temperature space.
    ratio = (np.sqrt(5.0) - 1.0) / 2.0
    x1, x2 = right - ratio * (right - left), left + ratio * (right - left)
    f1, f2 = score(x1), score(x2)
    for _ in range(28):
        if f1 <= f2:
            right, x2, f2 = x2, x1, f1
            x1 = right - ratio * (right - left)
            f1 = score(x1)
        else:
            left, x1, f1 = x1, x2, f2
            x2 = left + ratio * (right - left)
            f2 = score(x2)
    temperature = float(np.exp((left + right) / 2.0))
    valid_shape = _shape(fit_wavelengths, temperature)
    _, gain, offset = _linear_model(valid_shape, fit_values)

    # Evaluate the fitted model across the whole plot. Invalid extrapolated
    # wavelengths remain absent from the overlay.
    model = np.full(values.shape, np.nan, dtype=np.float64)
    model[valid] = offset + gain * valid_shape
    return BlackBodyFit(temperature, model)
