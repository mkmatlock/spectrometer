// Review processing runs entirely in the browser. Stored data is never modified.
const CHANNELS = ['Red', 'Green', 'Blue'];
const BAR_WIDTH = 462;
const BAR_HEIGHT = 38;

function bytes(value, label) {
  if (!value || value.encoding !== 'base64' || typeof value.data !== 'string') {
    throw new Error(`Invalid ${label}`);
  }
  const binary = atob(value.data);
  const result = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) result[i] = binary.charCodeAt(i);
  return result;
}

export function normalizeScalePoints(points) {
  const entries = Array.isArray(points) ? points : Object.entries(points || {});
  const unique = new Map();
  for (const pair of entries) {
    const pixel = Number(pair[0]);
    const value = Number(pair[1]);
    if (Number.isFinite(pixel) && Number.isFinite(value)) unique.set(pixel, value);
  }
  return [...unique].sort((a, b) => a[0] - b[0]);
}

function wavelength(pixel, points) {
  let segment = 0;
  while (segment < points.length - 2 && pixel >= points[segment + 1][0]) segment++;
  const [x0, y0] = points[segment];
  const [x1, y1] = points[segment + 1];
  return y0 + (pixel - x0) * (y1 - y0) / (x1 - x0);
}

export function pixelToWavelength(pixel, points) {
  const normalized = normalizeScalePoints(points);
  return normalized.length < 2 ? null : wavelength(pixel, normalized);
}

// A nonmonotonic calibration can have several inverse solutions; return the
// first in-range segment solution, followed by the two outer extrapolations.
export function wavelengthToPixel(value, points) {
  const normalized = normalizeScalePoints(points);
  if (normalized.length < 2) return null;
  for (let i = 0; i < normalized.length - 1; i++) {
    const [x0, y0] = normalized[i];
    const [x1, y1] = normalized[i + 1];
    if (y0 !== y1 && value >= Math.min(y0, y1) && value <= Math.max(y0, y1)) {
      return x0 + (value - y0) * (x1 - x0) / (y1 - y0);
    }
  }
  for (const i of [0, normalized.length - 2]) {
    const [x0, y0] = normalized[i];
    const [x1, y1] = normalized[i + 1];
    if (y0 === y1) continue;
    const pixel = x0 + (value - y0) * (x1 - x0) / (y1 - y0);
    if ((i === 0 && pixel < x0) || (i === normalized.length - 2 && pixel > x1)) return pixel;
  }
  return null;
}

export function axisTicks(points, start, end) {
  const normalized = normalizeScalePoints(points);
  if (normalized.length < 2 || end <= start) return [];
  const knots = [start, ...normalized.map(p => p[0]).filter(p => p > start && p < end), end];
  const ticks = new Map();
  for (let i = 0; i < knots.length - 1; i++) {
    const left = knots[i], right = knots[i + 1];
    const first = wavelength(left, normalized), last = wavelength(right, normalized);
    if (!Number.isFinite(first) || !Number.isFinite(last) || first === last) continue;
    const low = Math.ceil(Math.min(first, last) / 50) * 50;
    const high = Math.floor(Math.max(first, last) / 50) * 50;
    const count = Math.floor((high - low) / 50) + 1;
    // Malformed/extreme calibration should not freeze a browser. Retain ticks
    // at multiples of 50 nm, thinning only implausibly large sets.
    const step = 50 * Math.max(1, Math.ceil(count / 1000));
    for (let value = low, n = 0; value <= high && n < 1000; value += step, n++) {
      const pixel = left + (value - first) * (right - left) / (last - first);
      ticks.set(pixel.toFixed(8), [pixel, value]);
    }
  }
  return [...ticks.values()].sort((a, b) => a[0] - b[0]);
}

function imageReader(record, roi) {
  const config = record.instrument_settings.averaged_camera_format;
  const width = roi[2] - roi[0], height = roi[3] - roi[1];
  const image = record.averaged_camera_output;
  const data = bytes(image, 'averaged camera image');
  if (!config || image.dtype !== 'uint8' || config.format !== 'BGR888' ||
      String(image.shape) !== String([height, width, 3]) ||
      String(config.size) !== String([width, height]) ||
      String(config.origin) !== String(roi.slice(0, 2)) || data.length !== width * height * 3) {
    throw new Error('Invalid averaged camera image');
  }
  return (y, row) => {
    const offset = y * width * 3;
    for (let x = 0; x < width * 3; x += 3) {
      row[x] = data[offset + x + 2];
      row[x + 1] = data[offset + x + 1];
      row[x + 2] = data[offset + x];
    }
  };
}

function resizeWeights(size, target) {
  const scale = size / target;
  return Array.from({length: size}, (_, index) => {
    const result = [];
    const first = Math.floor(index / scale);
    const last = Math.min(target - 1, Math.ceil((index + 1) / scale) - 1);
    for (let output = first; output <= last; output++) {
      const overlap = Math.min(index + 1, (output + 1) * scale) - Math.max(index, output * scale);
      if (overlap > 0) result.push([output, overlap / scale]);
    }
    return result;
  });
}

export function prepareSpectrum(record) {
  if (!Array.isArray(record.spectrum_roi) || record.spectrum_roi.length !== 4 ||
      !record.spectrum_roi.every(v => Number.isInteger(v) && v >= 0)) {
    throw new Error('Invalid sensor area');
  }
  const roi = [...record.spectrum_roi];
  if (roi[2] <= roi[0] || roi[3] <= roi[1]) throw new Error('Invalid sensor area');
  const width = roi[2] - roi[0], height = roi[3] - roi[1];
  const savedIntensity = Float64Array.from(record.spectrum_intensity || []);
  if (savedIntensity.length !== width || !savedIntensity.every(Number.isFinite)) {
    throw new Error('Invalid spectrum intensity data');
  }
  const settings = record.instrument_settings;
  const calibration = settings?.calibration_settings;
  if (![settings, calibration, calibration?.scale, calibration?.channel_ranges].every(value =>
    value && typeof value === 'object' && !Array.isArray(value))) {
    throw new Error('Invalid calibration settings');
  }
  if (!Number.isFinite(record.spectrum_maximum) || record.spectrum_maximum <= 0) {
    throw new Error('Invalid spectrum maximum');
  }
  const scalePoints = normalizeScalePoints(calibration.scale);
  const savedRgb = bytes(record.spectrum_bar, 'camera slice');
  if (savedRgb.length !== BAR_WIDTH * BAR_HEIGHT * 3) throw new Error('Invalid camera slice');
  const reader = imageReader(record, roi);
  const channelSums = CHANNELS.map(() => new Float64Array(width));
  const accumulated = new Float64Array(BAR_WIDTH * BAR_HEIGHT * 3);
  const row = new Uint8Array(width * 3);
  const resizedRow = new Float64Array(BAR_WIDTH * 3);
  const xWeights = resizeWeights(width, BAR_WIDTH);
  const yWeights = resizeWeights(height, BAR_HEIGHT);
  for (let y = 0; y < height; y++) {
    reader(y, row);
    resizedRow.fill(0);
    for (let x = 0; x < width; x++) {
      const offset = x * 3;
      for (let c = 0; c < 3; c++) channelSums[c][x] += row[offset + c];
      for (const [target, weight] of xWeights[x]) {
        for (let c = 0; c < 3; c++) resizedRow[target * 3 + c] += row[offset + c] * weight;
      }
    }
    for (const [target, weight] of yWeights[y]) {
      const offset = target * BAR_WIDTH * 3;
      for (let i = 0; i < resizedRow.length; i++) accumulated[offset + i] += resizedRow[i] * weight;
    }
  }
  const rawRgb = Uint8ClampedArray.from(accumulated, Math.round);
  return {
    roi, scalePoints, savedIntensity, savedRgb: new Uint8ClampedArray(savedRgb), channelSums, rawRgb,
    maximum: record.spectrum_maximum,
  };
}

export function buildDisplay(prepared, enabledChannels = CHANNELS) {
  const channels = [...new Set(enabledChannels)];
  if (channels.some(channel => !CHANNELS.includes(channel))) throw new Error('Unknown color channel');
  const common = {
    width: BAR_WIDTH, height: BAR_HEIGHT, roi: prepared.roi,
    maximum: prepared.maximum, scalePoints: prepared.scalePoints,
  };
  // The saved aggregate includes channel calibration and is authoritative.
  if (channels.length === 3) {
    return {...common, intensity: prepared.savedIntensity, rgb: prepared.savedRgb};
  }
  const intensity = new Float64Array(prepared.savedIntensity.length);
  const rgb = new Uint8ClampedArray(BAR_WIDTH * BAR_HEIGHT * 3);
  if (channels.length) {
    for (const channel of channels) {
      const c = CHANNELS.indexOf(channel);
      const sums = prepared.channelSums[c];
      for (let x = 0; x < intensity.length; x++) intensity[x] += sums[x];
      for (let i = c; i < rgb.length; i += 3) rgb[i] = prepared.rawRgb[i];
    }
    common.maximum = (prepared.roi[3] - prepared.roi[1]) * 255 * channels.length;
  }
  return {...common, intensity, rgb};
}

export function featureIndices(intensity, absorption = false) {
  const indices = [];
  let start = 0;
  while (start < intensity.length) {
    let end = start;
    while (end + 1 < intensity.length && intensity[end + 1] === intensity[start]) end++;
    if (start > 0 && end < intensity.length - 1) {
      const value = intensity[start], before = intensity[start - 1], after = intensity[end + 1];
      if (absorption ? value < before && value < after : value > before && value > after) {
        indices.push(Math.floor((start + end) / 2));
      }
    }
    start = end + 1;
  }
  return indices;
}

export function findFeature(intensity, pixel, roi, absorption = false) {
  let selected = null, distance = Infinity;
  for (const index of featureIndices(intensity, absorption)) {
    const candidate = index + roi[0];
    if (Math.abs(candidate - pixel) < distance) {
      selected = candidate;
      distance = Math.abs(candidate - pixel);
    }
  }
  return selected;
}

function planckShape(wavelengths, temperature) {
  const shape = new Float64Array(wavelengths.length);
  let maximum = -Infinity;
  for (let i = 0; i < shape.length; i++) {
    const exponent = 1.438776877e-2 / (wavelengths[i] * temperature);
    const logDenominator = exponent < 50 ? Math.log(Math.expm1(exponent)) :
      exponent + Math.log1p(-Math.exp(-exponent));
    shape[i] = -5 * Math.log(wavelengths[i]) - logDenominator;
    maximum = Math.max(maximum, shape[i]);
  }
  for (let i = 0; i < shape.length; i++) shape[i] = Math.exp(shape[i] - maximum);
  return shape;
}

function linearModel(shape, values) {
  let shapeMean = 0, valueMean = 0;
  for (let i = 0; i < shape.length; i++) { shapeMean += shape[i]; valueMean += values[i]; }
  shapeMean /= shape.length;
  valueMean /= shape.length;
  let denominator = 0, numerator = 0;
  for (let i = 0; i < shape.length; i++) {
    denominator += (shape[i] - shapeMean) ** 2;
    numerator += (shape[i] - shapeMean) * (values[i] - valueMean);
  }
  const gain = denominator ? Math.max(0, numerator / denominator) : 0;
  const offset = valueMean - gain * shapeMean;
  let score = 0;
  for (let i = 0; i < shape.length; i++) score += (values[i] - offset - gain * shape[i]) ** 2;
  return {score, gain, offset};
}

export function fitBlackbody(intensity, roi, points) {
  const normalized = normalizeScalePoints(points);
  if (normalized.length < 2) throw new Error('Black Body requires wavelength calibration');
  const wavelengths = [], values = [], indices = [];
  let smallest = Infinity, largest = -Infinity;
  for (let i = 0; i < intensity.length; i++) {
    const metres = wavelength(i + roi[0], normalized) * 1e-9;
    if (Number.isFinite(intensity[i]) && Number.isFinite(metres) && metres > 0) {
      wavelengths.push(metres); values.push(intensity[i]); indices.push(i);
      smallest = Math.min(smallest, metres); largest = Math.max(largest, metres);
    }
  }
  if (values.length < 8 || smallest === largest) {
    throw new Error('Wavelength calibration does not cover the spectrum');
  }
  const score = logT => linearModel(planckShape(wavelengths, Math.exp(logT)), values).score;
  const low = Math.log(100), high = Math.log(100000), increment = (high - low) / 95;
  let best = 0, bestScore = Infinity;
  for (let i = 0; i < 96; i++) {
    const candidate = score(low + i * increment);
    if (candidate < bestScore) { best = i; bestScore = candidate; }
  }
  let left = low + Math.max(0, best - 1) * increment;
  let right = low + Math.min(95, best + 1) * increment;
  const ratio = (Math.sqrt(5) - 1) / 2;
  let x1 = right - ratio * (right - left), x2 = left + ratio * (right - left);
  let f1 = score(x1), f2 = score(x2);
  for (let i = 0; i < 28; i++) {
    if (f1 <= f2) {
      right = x2; x2 = x1; f2 = f1;
      x1 = right - ratio * (right - left); f1 = score(x1);
    } else {
      left = x1; x1 = x2; f1 = f2;
      x2 = left + ratio * (right - left); f2 = score(x2);
    }
  }
  const temperature = Math.exp((left + right) / 2);
  const shape = planckShape(wavelengths, temperature);
  const {gain, offset} = linearModel(shape, values);
  const model = new Float64Array(intensity.length).fill(NaN);
  for (let i = 0; i < indices.length; i++) model[indices[i]] = offset + gain * shape[i];
  return {temperature, intensity: model};
}
