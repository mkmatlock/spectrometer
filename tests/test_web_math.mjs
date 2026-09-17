import test from 'node:test';
import assert from 'node:assert/strict';
import {
  prepareSpectrum, buildDisplay, pixelToWavelength, wavelengthToPixel,
  axisTicks, featureIndices, findFeature, fitBlackbody,
} from '../spectrometer/web/spectrum-math.js';

const encoded = (data, shape) => ({
  encoding: 'base64', data: Buffer.from(data).toString('base64'),
  ...(shape ? {dtype: 'uint8', shape} : {}),
});

function averagedRecord() {
  return {
    spectrum_roi: [10, 20, 14, 22],
    spectrum_intensity: [5, 6, 7, 8], // Persisted calibrated aggregate is authoritative.
    spectrum_bar: encoded(new Uint8Array(462 * 38 * 3).fill(11)),
    averaged_camera_output: encoded([
      1, 10, 100, 2, 20, 110, 3, 30, 120, 4, 40, 130,
      5, 50, 140, 6, 60, 150, 7, 70, 160, 8, 80, 170,
    ], [2, 4, 3]),
    instrument_settings: {
      averaged_camera_format: {format: 'BGR888', size: [4, 2], origin: [10, 20]},
      intensity_calculation: 'rgb_channel_sum',
      calibration_settings: {scale: {'10': 700, '13': 400}, channel_ranges: {Red: [12, 13]}},
    },
  };
}

test('averaged BGR image channels, aggregate, preview and disabled channels match Review', () => {
  const record = averagedRecord();
  const original = structuredClone(record);
  const prepared = prepareSpectrum(record);
  const all = buildDisplay(prepared);
  assert.deepEqual([...all.intensity], [5, 6, 7, 8]);
  assert.equal(all.maximum, 2 * 255 * 3);
  assert.equal(all.rgb[0], 11);
  assert.deepEqual(all.scalePoints, [[10, 700], [13, 400]]);
  const redBlue = buildDisplay(prepared, ['Red', 'Blue']);
  assert.deepEqual([...redBlue.intensity], [246, 268, 290, 312]);
  assert.deepEqual([...redBlue.rgb.slice(0, 3)], [100, 0, 1]);
  assert.deepEqual([...redBlue.rgb.slice(-3)], [170, 0, 8]);
  assert.equal(redBlue.maximum, 2 * 255 * 2);
  const none = buildDisplay(prepared, []);
  assert.ok(none.intensity.every(value => value === 0));
  assert.ok(none.rgb.every(value => value === 0));
  assert.equal(none.maximum, all.maximum);
  assert.deepEqual(record, original);
});

test('legacy all-channel capture retains its saved grayscale intensity and scale', () => {
  const record = averagedRecord();
  delete record.instrument_settings.intensity_calculation;
  delete record.instrument_settings.calibration_settings;
  record.calibration_settings = {scale: {'10': 400, '13': 900}};
  const display = buildDisplay(prepareSpectrum(record));
  assert.deepEqual([...display.intensity], record.spectrum_intensity);
  assert.equal(display.maximum, 2 * 255);
  assert.deepEqual(display.scalePoints, [[10, 400], [13, 900]]);
});

test('all packed Bayer patterns reproduce OpenCV column sums, including borders, stride and ROI', () => {
  const bayer = [11, 33, 21, 44, 66, 122, 77, 144, 55, 99, 44, 22, 155, 233, 255, 77];
  // Expected values generated with cv2.cvtColor(..., COLOR_Bayer{pattern}2RGB).
  const expected = {
    RGGB: [[166, 336, 600], [166, 336, 600], [154, 380, 554], [154, 380, 554]],
    BGGR: [[600, 336, 166], [600, 336, 166], [554, 380, 154], [554, 380, 154]],
    GRBG: [[330, 472, 420], [330, 472, 420], [222, 254, 486], [222, 254, 486]],
    GBRG: [[420, 472, 330], [420, 472, 330], [486, 254, 222], [486, 254, 222]],
  };
  const packed = new Uint8Array(8 * 16).fill(255);
  for (let y = 0; y < 4; y++) {
    for (let x = 0; x < 4; x += 2) {
      const offset = (y + 2) * 16 + (x + 2) * 3 / 2;
      packed[offset] = bayer[y * 4 + x];
      packed[offset + 1] = bayer[y * 4 + x + 1];
      packed[offset + 2] = 7; // Low bits must not affect the 8-bit preview.
    }
  }
  for (const [pattern, totals] of Object.entries(expected)) {
    const record = {
      spectrum_roi: [2, 2, 6, 6], spectrum_intensity: [1, 2, 3, 4],
      raw_camera_output: encoded(packed, [8, 16]),
      instrument_settings: {raw_camera_format: {format: `S${pattern}12_CSI2P`, size: [8, 8], stride: 16}},
    };
    const prepared = prepareSpectrum(record);
    for (const [index, channel] of ['Red', 'Green', 'Blue'].entries()) {
      assert.deepEqual([...buildDisplay(prepared, [channel]).intensity], totals.map(row => row[index]), pattern);
    }
    assert.equal(buildDisplay(prepared).rgb.length, 462 * 38 * 3);
  }
});

test('missing channel source permits saved view and reports channel filter error', () => {
  const record = averagedRecord();
  delete record.averaged_camera_output;
  const prepared = prepareSpectrum(record);
  assert.deepEqual([...buildDisplay(prepared).intensity], record.spectrum_intensity);
  assert.throws(() => buildDisplay(prepared, ['Red']), /no camera image/);
  assert.throws(() => buildDisplay(prepared, ['Orange']), /Unknown color/);
});

test('invalid image buffers and nonfinite spectrum data are rejected', () => {
  const record = averagedRecord();
  record.averaged_camera_output.shape = [4, 2, 3];
  assert.throws(() => prepareSpectrum(record), /Invalid averaged/);
  record.averaged_camera_output.shape = [2, 4, 3];
  record.spectrum_intensity[0] = NaN;
  assert.throws(() => prepareSpectrum(record), /Invalid spectrum intensity/);
});

test('piecewise wavelength interpolation, extrapolation and nonmonotonic 50 nm ticks', () => {
  const points = [[0, 400], [10, 600], [20, 400]];
  assert.equal(pixelToWavelength(-5, points), 300);
  assert.equal(pixelToWavelength(5, points), 500);
  assert.equal(pixelToWavelength(25, points), 300);
  assert.equal(wavelengthToPixel(500, points), 5);
  assert.equal(pixelToWavelength(5, [[0, 400]]), null);
  assert.deepEqual(axisTicks(points, 0, 20), [
    [0, 400], [2.5, 450], [5, 500], [7.5, 550], [10, 600],
    [12.5, 550], [15, 500], [17.5, 450], [20, 400],
  ]);
  assert.ok(axisTicks([[0, -1e20], [20, 1e20]], 0, 20).length <= 1000);
});

test('peak and valley selection ignores endpoints and centers flat features', () => {
  const intensity = [100, 1, 5, 5, 5, 1, 7, 0, 100];
  assert.deepEqual(featureIndices(intensity), [3, 6]);
  assert.deepEqual(featureIndices(intensity, true), [1, 5, 7]);
  assert.equal(findFeature(intensity, 14, [10, 0, 19, 1]), 13);
  assert.equal(findFeature(intensity, 14, [10, 0, 19, 1], true), 15);
  assert.equal(findFeature([1, 1, 1], 1, [0, 0, 3, 1]), null);
});

test('Planck fit recovers detector gain, background and temperature', () => {
  const points = [[0, 400], [3499, 900]];
  const shape = Float64Array.from({length: 3500}, (_, i) => {
    const metres = (400 + i * 500 / 3499) * 1e-9;
    return 1 / (metres ** 5 * Math.expm1(1.438776877e-2 / (metres * 4200)));
  });
  const maximum = Math.max(...shape);
  const values = Float64Array.from(shape, value => 1200 + 50000 * value / maximum);
  const fit = fitBlackbody(values, [0, 0, 3500, 250], points);
  assert.ok(Math.abs(fit.temperature - 4200) < 2, String(fit.temperature));
  for (let i = 0; i < values.length; i++) assert.ok(Math.abs(fit.intensity[i] - values[i]) < values[i] * 2e-4);
  assert.throws(() => fitBlackbody(values, [0, 0, 3500, 250], []), /requires wavelength calibration/);
  assert.throws(() => fitBlackbody(values, [0, 0, 3500, 250], [[0, -900], [3499, -400]]), /does not cover/);
});
