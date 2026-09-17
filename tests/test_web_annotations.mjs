import test from 'node:test';
import assert from 'node:assert/strict';
import {drawPeakLabels, normalizePeakLabels, hitTestPeakLabel} from '../spectrometer/web/annotations.js';

function context() {
  const ctx = {operations: [], font: 'original', fillStyle: 'original fill', strokeStyle: 'original stroke',
    lineWidth: 9, textAlign: 'left', textBaseline: 'bottom'};
  const states = [];
  const fields = ['font', 'fillStyle', 'strokeStyle', 'lineWidth', 'textAlign', 'textBaseline'];
  ctx.save = () => { states.push(Object.fromEntries(fields.map(key => [key, ctx[key]]))); ctx.operations.push(['save']); };
  ctx.restore = () => { Object.assign(ctx, states.pop()); ctx.operations.push(['restore']); };
  ctx.measureText = text => ({width: [...text].length * 7});
  for (const method of ['beginPath', 'rect', 'clip', 'moveTo', 'lineTo', 'stroke', 'arc', 'fillRect', 'strokeRect', 'fillText']) {
    ctx[method] = (...args) => ctx.operations.push([method, ...args]);
  }
  return ctx;
}

function bounds(left = 24, right = 456, top = 16, bottom = 328) {
  return {left, right, top, bottom,
    x: pixel => right - (pixel - 1000) / 99 * (right - left),
    y: value => bottom - Math.max(0, Math.min(1, value / 100)) * (bottom - top)};
}
const roi = [1000, 500, 1100, 550];
const flat = new Float64Array(100).fill(50);
const overlaps = (a, b) => a.x < b.x + b.width && a.x + a.width > b.x
  && a.y < b.y + b.height && a.y + a.height > b.y;

test('optional labels are safely normalized to canonical pixels inside the ROI', () => {
  assert.deepEqual(normalizePeakLabels(undefined, roi), {});
  assert.deepEqual(normalizePeakLabels(null, roi), {});
  assert.deepEqual(normalizePeakLabels(['Label'], roi), {});
  const input = JSON.parse('{"1000":"Red", "1099":"Blue", "1100":"Outside", "999":"Outside", "01001":"Leading zero", "1001.0":"Decimal", "1e3":"Exponent", "__proto__":"Unsafe"}');
  assert.deepEqual(normalizePeakLabels(input, roi), {'1000': 'Red', '1099': 'Blue'});
  assert.deepEqual(normalizePeakLabels({1000: '', 1001: '   ', 1002: 5, 1003: 'a'.repeat(65)}, roi), {});
  assert.deepEqual(normalizePeakLabels({1000: '🌈'.repeat(64)}, roi), {1000: '🌈'.repeat(64)});
  assert.deepEqual(normalizePeakLabels({1000: '🌈'.repeat(65)}, roi), {});
  assert.deepEqual(normalizePeakLabels({1000: 'Line'}, null), {});
});

test('sensor coordinates and reversed X direction locate endpoint labels and circles', () => {
  const ctx = context(), values = new Float64Array(flat);
  values[0] = -1; values[99] = 101;
  const boxes = drawPeakLabels(ctx, bounds(), values, roi, {1000: 'Red', 1099: 'Blue', 1100: 'Outside'});
  assert.deepEqual(boxes.map(box => box.pixel), [1000, 1099]);
  assert.ok(boxes[0].x > boxes[1].x);
  assert.deepEqual(ctx.operations.filter(op => op[0] === 'arc').map(op => op.slice(1, 3)), [[456, 328], [24, 16]]);
  for (const box of boxes) {
    assert.ok(box.x >= 24 && box.x + box.width <= 456);
    assert.ok(box.y >= 16 && box.y + box.height <= 328);
    assert.equal(hitTestPeakLabel(boxes, box.x + box.width / 2, box.y + box.height / 2), box);
  }
});

test('neighboring long labels are truncated, assigned separate rows and laid out deterministically', () => {
  const ctx = context(), labels = {1048: 'A'.repeat(64), 1049: 'B'.repeat(64), 1050: 'C'.repeat(64)};
  const original = structuredClone(labels);
  const boxes = drawPeakLabels(ctx, bounds(), flat, roi, labels);
  assert.deepEqual(labels, original);
  for (let i = 0; i < boxes.length; i++) {
    assert.ok(boxes[i].width <= 188);
    for (let j = i + 1; j < boxes.length; j++) assert.equal(overlaps(boxes[i], boxes[j]), false);
  }
  const strings = ctx.operations.filter(op => op[0] === 'fillText').map(op => op[1]);
  assert.ok(strings.every(text => text.endsWith('…') && text.length < 64));
  assert.deepEqual(drawPeakLabels(context(), bounds(), flat, roi, Object.fromEntries(Object.entries(labels).reverse())), boxes);
});

test('annotations remain visible for valleys, flat values and all channels off', () => {
  const ctx = context(), values = new Float64Array(flat); values[60] = 0;
  assert.deepEqual(drawPeakLabels(ctx, bounds(), values, roi, {1040: 'Flat', 1060: 'Valley'}).map(box => box.pixel), [1040, 1060]);
  assert.equal(drawPeakLabels(ctx, bounds(), new Float64Array(100), roi, {1040: 'Flat', 1060: 'Valley'}).length, 2);
});

test('drawing clips to the graph, paints leaders before text boxes and restores caller state', () => {
  const ctx = context();
  drawPeakLabels(ctx, bounds(), flat, roi, {1040: 'A', 1050: 'B'});
  assert.equal(ctx.font, 'original'); assert.equal(ctx.fillStyle, 'original fill');
  assert.equal(ctx.strokeStyle, 'original stroke'); assert.equal(ctx.lineWidth, 9);
  assert.equal(ctx.textAlign, 'left'); assert.equal(ctx.textBaseline, 'bottom');
  const names = ctx.operations.map(op => op[0]);
  assert.ok(names.indexOf('clip') < names.indexOf('arc'));
  assert.ok(names.lastIndexOf('arc') < names.indexOf('fillRect'));
  assert.equal(names[0], 'save'); assert.equal(names.at(-1), 'restore');
  assert.ok(ctx.operations.some(op => JSON.stringify(op) === JSON.stringify(['rect', 24, 16, 432, 312])));
});

test('tiny graphs clip every target and topmost labels win when overlap is unavoidable', () => {
  const small = bounds(24, 29, 16, 20), ctx = context();
  const boxes = drawPeakLabels(ctx, small, flat, roi, {1040: 'Long', 1050: 'Text'});
  assert.equal(boxes.length, 2);
  for (const box of boxes) {
    assert.ok(box.x >= 24 && box.x + box.width <= 29);
    assert.ok(box.y >= 16 && box.y + box.height <= 20);
  }
  assert.equal(hitTestPeakLabel(boxes, 26, 18), boxes.at(-1));
  assert.equal(hitTestPeakLabel(boxes, 23, 18), null);
  assert.equal(hitTestPeakLabel([], 26, 18), null);
});

test('empty or invalid graph data has no hit targets', () => {
  const ctx = context();
  assert.deepEqual(drawPeakLabels(ctx, bounds(), [], roi, {1040: 'Line'}), []);
  assert.deepEqual(drawPeakLabels(ctx, bounds(24, 24), flat, roi, {1040: 'Line'}), []);
  assert.deepEqual(drawPeakLabels(ctx, bounds(24, NaN), flat, roi, {1040: 'Line'}), []);
  assert.deepEqual(drawPeakLabels(ctx, bounds(), [NaN], roi, {1000: 'Line'}), []);
});

test('caller state is restored if a canvas operation fails', () => {
  const ctx = context();
  ctx.fillText = () => { throw new Error('Draw failure'); };
  assert.throws(() => drawPeakLabels(ctx, bounds(), flat, roi, {1040: 'Line'}), /Draw failure/);
  assert.equal(ctx.font, 'original'); assert.equal(ctx.operations.at(-1)[0], 'restore');
});
