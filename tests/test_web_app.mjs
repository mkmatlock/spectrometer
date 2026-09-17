import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Exercise the real controller and spectrum processing without a browser or
// third-party DOM. The mock only supplies DOM/canvas methods used by app.js.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.attributes = new Map();
    this.dataset = {}; this.listeners = new Map(); this.value = ''; this.textContent = '';
    this.className = ''; this.hidden = false; this.disabled = false; this.open = false;
    this.classList = {toggle: (name, force) => {
      const names = new Set(this.className.split(' ').filter(Boolean));
      const enabled = force ?? !names.has(name);
      if (enabled) names.add(name); else names.delete(name);
      this.className = [...names].join(' '); return enabled;
    }, contains: name => this.className.split(' ').includes(name)};
    this.context = new Proxy({arcs: [], texts: [], image: null,
      measureText: text => ({width: String(text).length * 7}),
      arc: (...args) => this.context.arcs.push(args),
      fillText: (text, x, y) => this.context.texts.push({text: String(text), x, y}),
      clearRect: () => { this.context.arcs = []; this.context.texts = []; },
      putImageData: image => { this.context.image = image; },
    }, {get: (target, name) => target[name] ?? (() => {})});
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  querySelector(selector) {
    if (selector === '.selected') return this.children.find(element => element.classList.contains('selected')) || null;
    throw new Error(`Unsupported selector: ${selector}`);
  }
  toggleAttribute(name, force) {
    if (force ?? !this.attributes.has(name)) this.attributes.set(name, '');
    else this.attributes.delete(name);
  }
  addEventListener(type, handler) {
    this.listeners.set(type, [...(this.listeners.get(type) || []), handler]);
  }
  async dispatch(type, detail = {}) {
    if (this.disabled) return;
    const event = {preventDefault() { this.defaultPrevented = true; }, ...detail};
    const handlers = [this['on' + type], ...(this.listeners.get(type) || [])].filter(Boolean);
    await Promise.all(handlers.map(handler => handler(event)));
    if (type === 'cancel' && !event.defaultPrevented) this.close();
    return event;
  }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  select() {}
  scrollIntoView() {}
  getBoundingClientRect() { return {width: 480, height: 360, left: 0, top: 0}; }
  getContext() { return this.context; }
}

function makeDOM(html) {
  const elements = [], ids = new Map();
  // IDs, disabled flags and data attributes come from the shipped HTML, so a
  // missing/renamed controller hook fails rather than being silently invented.
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)) {
    const [, tag, attributes] = match;
    const element = new Element(tag);
    for (const [, name, value = ''] of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      element.setAttribute(name, value);
      if (name === 'id') ids.set(value, element);
      if (name === 'class') element.className = value;
      if (name === 'hidden' || name === 'disabled') element[name] = true;
      if (name.startsWith('data-')) element.dataset[name.slice(5)] = value;
    }
    elements.push(element);
  }
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  return {
    ids, elements,
    getElementById: id => ids.get(id) || null,
    createElement: tag => new Element(tag),
    querySelectorAll(selector) {
      if (selector === '[data-channel]') return elements.filter(e => 'channel' in e.dataset);
      if (selector === '.mode-toggle') return elements.filter(e => e.classList.contains('mode-toggle'));
      if (selector === '[data-action], .record') return [
        ...elements.filter(e => 'action' in e.dataset),
        ...descendants(ids.get('spectrum-list')).filter(e => e.classList.contains('record')),
      ];
      if (selector === '#name-dialog button, #name-input, #delete-dialog button') {
        return [ids.get('name-input'), ids.get('name-cancel'), ids.get('delete-cancel'), ids.get('delete-confirm')];
      }
      throw new Error(`Unsupported selector: ${selector}`);
    },
  };
}

function spectrum(name = 'Lamp', calibrated = true) {
  const values = [1, 8, 2, 4, 10, 3, 1, 9, 2];
  const pixels = [...values, ...values].flatMap(v => [v, v * 2, v * 3]);
  return {
    name, timestamp: '2026-09-16T12:00:00+00:00', spectrum_roi: [0, 0, 9, 2], peak_labels: {},
    spectrum_intensity: values.map(v => v * 12),
    spectrum_maximum: 2 * 255 * 3,
    spectrum_bar: {encoding: 'base64', data: Buffer.from(new Uint8Array(462 * 38 * 3).fill(11)).toString('base64')},
    averaged_camera_output: {encoding: 'base64', dtype: 'uint8', shape: [2, 9, 3],
      data: Buffer.from(pixels).toString('base64')},
    instrument_settings: {
      averaged_camera_format: {format: 'BGR888', size: [9, 2], origin: [0, 0]},
      intensity_calculation: 'rgb_channel_sum',
      calibration_settings: {scale: calibrated ? {0: 800, 8: 400} : {}, channel_ranges: {}},
    },
  };
}
const settingsFixture = {
  camera: {frame_rate: 5, resolution: [4056, 3040], exposure_us: 12000, frame_averaging: 3},
  calibration: {sensor_area: [0, 1550, 3500, 1800], scale: {100: 650, 500: 450}, channel_ranges: {Red: [0, 2000]}},
  network: {ip_address: '192.168.1.163', wifi_ssid: 'Lab Wi-Fi'},
};
const response = (status, data) => ({status, ok: status >= 200 && status < 300,
  json: async () => structuredClone(data)});
const allText = node => [node.textContent, ...node.children.map(allText)].join(' ');
const allNodes = node => [node, ...node.children.flatMap(allNodes)];
let instance = 0;

async function launch() {
  const html = await readFile(new URL('../spectrometer/web/index.html', import.meta.url), 'utf8');
  const dom = makeDOM(html), calls = [], records = new Map([[1000, spectrum('Old lamp', false)], [2000, spectrum('New lamp')]]);
  const overrides = new Map();
  const fetchFixture = async (path, options = {}) => {
    const method = options.method || 'GET';
    calls.push({method, path, body: options.body});
    const override = overrides.get(`${method} ${path}`);
    if (override) return typeof override === 'function' ? override() : override;
    if (path === '/list') return response(200, [...records].map(([id, record]) =>
      ({id, name: record.name, timestamp: `2026-09-16 12:00:0${id / 1000}`})));
    if (path === '/settings') return response(200, settingsFixture);
    if (path.startsWith('/capture?')) {
      const name = new URL(path, 'http://spectrometer').searchParams.get('name');
      records.set(3000, spectrum(name)); return response(200, 3000);
    }
    const match = /^\/spectrum\/(\d+)$/.exec(path), id = Number(match?.[1]);
    if (!records.has(id)) return response(404, {error: 'Spectrum not found'});
    if (method === 'PATCH') {
      const update = JSON.parse(options.body);
      if ('peak_label' in update) {
        const {pixel, label} = update.peak_label;
        const labels = records.get(id).peak_labels;
        if (label === null) delete labels[pixel]; else labels[pixel] = label;
        return response(200, {id, peak_labels: labels});
      }
      records.get(id).name = update.name;
      return response(200, {id, name: update.name});
    }
    if (method === 'DELETE') { records.delete(id); return response(204, null); }
    return response(200, records.get(id));
  };
  Object.assign(globalThis, {
    document: dom, window: {devicePixelRatio: 1}, fetch: fetchFixture,
    requestAnimationFrame: callback => { callback(); return 1; },
    matchMedia: () => ({matches: false}),
    ResizeObserver: class { observe() {} },
    ImageData: class { constructor(data, width, height) { Object.assign(this, {data, width, height}); } },
  });
  const source = (await readFile(new URL('../spectrometer/web/app.js', import.meta.url), 'utf8'))
    .replace(/'\.\/([\w-]+\.js)'/g, (_, module) => JSON.stringify(new URL(`../spectrometer/web/${module}`, import.meta.url).href));
  await import(`data:text/javascript;base64,${Buffer.from(source + `\n//# sourceURL=app-controller-${++instance}.js`).toString('base64')}`);
  for (let i = 0; i < 20 && dom.ids.get('review-view').getAttribute('aria-busy') === 'true'; i++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.equal(dom.ids.get('review-view').getAttribute('aria-busy'), 'false');
  const $ = id => { const element = dom.ids.get(id); assert.ok(element, id); return element; };
  return {dom, $, calls, overrides, records,
    click: id => $(id).dispatch('click'),
    select: index => $('spectrum-list').children[index].dispatch('click'),
    submit: name => { $('name-input').value = name; return $('name-form').dispatch('submit'); },
  };
}

test('review sorts and selects recordings, displays the saved spectrum, and marks calibrated peaks and valleys', async () => {
  const app = await launch(), {$, calls} = app;
  assert.equal($('display').disabled, true);
  assert.equal($('rename').disabled, true);
  assert.ok($('spectrum-modes'));
  for (const id of ['modes', 'modes-dialog', 'modes-back', 'back', 'review-nav', 'settings-view', 'settings-back']) {
    assert.equal(app.dom.ids.has(id), false, `${id} is removed`);
  }
  const modeButtons = [...app.dom.querySelectorAll('[data-channel]'), $('emission-toggle'), $('blackbody-toggle')];
  assert.ok(modeButtons.every(button => button.disabled), 'inline modes require a displayed spectrum');
  assert.match(allText($('spectrum-list').children[0]), /New lamp/);
  await app.select(0);
  assert.equal($('display').disabled, false);
  assert.equal($('rename').disabled, true, 'selecting a list entry does not enable viewer actions');
  await app.click('display');
  assert.deepEqual(calls.map(call => call.path), ['/list', '/spectrum/2000']);
  assert.equal($('record-name').textContent, 'New lamp');
  assert.equal($('spectrum-view').hidden, false);
  assert.equal($('rename').disabled, false);
  assert.ok(modeButtons.every(button => !button.disabled), 'inline modes are ready without opening a dialog');
  assert.equal($('axis-label').textContent, 'Wavelength (nm)');
  assert.ok($('slice').context.image.data.some(value => value));
  await $('plot').dispatch('click', {clientX: 240, clientY: 304});
  assert.equal($('peak-label').textContent, '600.0 nm');
  assert.ok($('plot').context.arcs.length > 0, 'peak marker is drawn');
  const red = app.dom.querySelectorAll('[data-channel]')[0];
  await red.dispatch('click');
  assert.equal(red.getAttribute('aria-pressed'), 'false');
  assert.equal($('channel-label').textContent, 'Green · Blue');
  const rgba = $('slice').context.image.data;
  assert.ok(rgba.every((value, i) => i % 4 !== 0 || value === 0), 'red removed from camera slice');
  await app.click('emission-toggle');
  assert.equal($('mode-label').textContent, 'Absorption');
  await $('plot').dispatch('click', {clientX: 132, clientY: 323});
  assert.equal($('peak-label').textContent, '500.0 nm');
  await app.click('blackbody-toggle');
  assert.equal($('blackbody-toggle').getAttribute('aria-pressed'), 'true');
  assert.match($('temperature').textContent, /^T: [\d,]+ K$/);
  await app.click('blackbody-toggle');
  assert.equal($('temperature').textContent, '');
  assert.equal(calls.length, 2, 'display modes never write the recording');
  await app.select(1); await app.click('display');
  assert.equal($('axis-label').textContent, 'Pixel');
  await $('plot').dispatch('keydown', {key: 'ArrowRight'});
  assert.match($('peak-label').textContent, /^Pixel \d+$/);
  await app.click('blackbody-toggle');
  assert.equal($('fit-error').hidden, false);
  assert.match($('fit-error').textContent, /requires wavelength calibration/);
});

test('capture submits the name, fetches the new ID, and displays the newly saved spectrum', async () => {
  const app = await launch(), {$, calls} = app;
  await app.click('capture-nav');
  assert.equal($('name-dialog').open, true);
  assert.equal($('name-input').value, '');
  await app.submit('  Lamp & λ  ');
  assert.deepEqual(calls.slice(1).map(call => call.path), ['/capture?name=Lamp%20%26%20%CE%BB', '/spectrum/3000']);
  assert.equal($('name-dialog').open, false);
  assert.equal($('record-name').textContent, 'Lamp & λ');
  assert.match(allText($('spectrum-list').children[0]), /Lamp & λ/);
  assert.equal($('review-view').hidden, false);
});

test('rename prepopulates the current name and updates both list and displayed title; deletion requires confirmation', async () => {
  const app = await launch(), {$, calls} = app;
  await app.select(0); await app.click('display'); await app.click('rename');
  assert.equal($('name-input').value, 'New lamp');
  await app.submit('Renamed lamp');
  assert.deepEqual(calls.at(-1), {method: 'PATCH', path: '/spectrum/2000', body: '{"name":"Renamed lamp"}'});
  assert.equal($('record-name').textContent, 'Renamed lamp');
  assert.match(allText($('spectrum-list').children[0]), /Renamed lamp/);
  await app.click('delete');
  assert.equal($('delete-dialog').open, true);
  assert.equal($('delete-name').textContent, 'Renamed lamp');
  const beforeCancel = calls.length;
  await app.click('delete-cancel');
  assert.equal(calls.length, beforeCancel);
  assert.equal($('delete-dialog').open, false);
  await app.click('delete'); await app.click('delete-confirm');
  assert.equal(calls.at(-1).method, 'DELETE');
  assert.equal(calls.at(-1).path, '/spectrum/2000');
  assert.equal($('delete-dialog').open, false);
  assert.equal($('spectrum-view').hidden, true);
  assert.equal($('spectrum-list').children.length, 1);
  assert.equal($('display').disabled, true);
  assert.equal($('rename').disabled, true);
});

test('rename targets the displayed spectrum even when another list entry is selected', async () => {
  const app = await launch(), {$, calls, records} = app;
  await app.select(0); await app.click('display');
  await app.select(1);
  await app.click('rename');
  assert.equal($('name-input').value, 'New lamp');
  await app.submit('Displayed lamp');
  assert.deepEqual(calls.at(-1), {method: 'PATCH', path: '/spectrum/2000', body: '{"name":"Displayed lamp"}'});
  assert.equal($('record-name').textContent, 'Displayed lamp');
  assert.equal(records.get(2000).name, 'Displayed lamp');
  assert.equal(records.get(1000).name, 'Old lamp');
  assert.equal($('spectrum-list').children[1].getAttribute('aria-pressed'), 'true');
  await app.click('display');
  assert.equal($('record-name').textContent, 'Old lamp');
});

test('settings show read-only API values in a dismissible dialog and preserve the underlying spectrum', async () => {
  const app = await launch(), {$, calls} = app;
  await app.select(0); await app.click('display');
  await app.dom.querySelectorAll('[data-channel]')[0].dispatch('click');
  await app.click('emission-toggle');
  await $('plot').dispatch('click', {clientX: 132, clientY: 323});
  await app.select(1);
  const spectrumRequests = calls.filter(call => call.path.startsWith('/spectrum/')).length;
  await app.click('settings-nav');
  assert.equal(calls.at(-1).path, '/settings');
  assert.equal($('settings-dialog').tagName, 'DIALOG');
  assert.equal($('settings-dialog').open, true);
  assert.equal($('review-view').hidden, false);
  assert.equal($('spectrum-view').hidden, false);
  const text = allText($('settings-content'));
  for (const value of ['5 fps', '4056 × 3040', '12 ms', '3 frames', '192.168.1.163',
    'Lab Wi-Fi', '0, 1550, 3500, 1800', 'Pixel 100', '650 nm', '0 – 2000']) assert.ok(text.includes(value), value);
  assert.ok(allNodes($('settings-content')).every(node => !['INPUT', 'SELECT', 'BUTTON'].includes(node.tagName)));
  const dismissals = [() => app.click('settings-close'), () => $('settings-dialog').dispatch('cancel')];
  for (const [index, close] of dismissals.entries()) {
    await close();
    assert.equal($('settings-dialog').open, false);
    assert.equal($('record-name').textContent, 'New lamp');
    assert.equal($('channel-label').textContent, 'Green · Blue');
    assert.equal($('mode-label').textContent, 'Absorption');
    assert.equal($('peak-label').textContent, '500.0 nm');
    assert.equal($('feature-dialog').hidden, false);
    assert.equal($('spectrum-list').children[1].getAttribute('aria-pressed'), 'true');
    assert.equal(calls.filter(call => call.path.startsWith('/spectrum/')).length, spectrumRequests);
    if (index === 0) {
      await app.click('settings-nav');
      assert.equal($('settings-dialog').open, true);
    }
  }
  assert.ok(calls.every(call => call.method === 'GET'));
});

test('name cancellation and invalid input make no request; server errors preserve the active recording and recover controls', async () => {
  const app = await launch(), {$, calls, overrides} = app;
  await app.click('capture-nav'); await app.click('name-cancel');
  assert.equal($('name-dialog').open, false);
  assert.equal(calls.length, 1);
  await app.click('capture-nav');
  await app.submit('   '); await app.submit('x'.repeat(65));
  assert.equal(calls.length, 1);
  assert.match($('name-error').textContent, /1–64/);
  overrides.set('GET /capture?name=Busy', response(409, {error: 'Camera paused or capture busy'}));
  await app.submit('Busy');
  assert.equal($('name-dialog').open, true);
  assert.match($('name-error').textContent, /Camera paused/);
  assert.equal($('name-input').disabled, false);
  await app.click('name-cancel'); await app.select(0); await app.click('display');
  overrides.set('PATCH /spectrum/2000', response(500, {error: 'Cannot rename spectrum'}));
  await app.click('rename'); await app.submit('Failed name');
  assert.equal($('record-name').textContent, 'New lamp');
  assert.equal($('name-dialog').open, true);
  assert.match($('name-error').textContent, /Cannot rename/);
  await app.click('name-cancel');
  overrides.set('DELETE /spectrum/2000', response(500, {error: 'Cannot delete spectrum'}));
  await app.click('delete'); await app.click('delete-confirm');
  assert.equal($('delete-dialog').open, true);
  assert.equal($('spectrum-view').hidden, false);
  assert.match($('delete-error').textContent, /Cannot delete/);
  assert.equal($('delete-confirm').disabled, false);
  await app.click('delete-cancel');
  overrides.set('GET /list', () => { throw new TypeError('Network error'); });
  await app.click('refresh');
  assert.match($('status').textContent, /Cannot reach the spectrometer/);
  assert.equal($('refresh').disabled, false);
});

test('a saved capture remains listed when viewing fails and cannot be accidentally resubmitted', async () => {
  const app = await launch(), {$, calls, overrides} = app;
  overrides.set('GET /spectrum/3000', response(503, {error: 'Another spectrum transfer is in progress'}));
  await app.click('capture-nav'); await app.submit('Saved lamp');
  assert.equal($('name-dialog').open, false);
  assert.match(allText($('spectrum-list').children[0]), /Saved lamp/);
  assert.match($('status').textContent, /transfer is in progress/);
  assert.equal(calls.filter(call => call.path.startsWith('/capture')).length, 1);
  overrides.delete('GET /spectrum/3000');
  await app.click('display');
  assert.equal($('record-name').textContent, 'Saved lamp');
  assert.equal(calls.filter(call => call.path.startsWith('/capture')).length, 1);
});

async function openNewest(app) {
  await app.select(0);
  await app.click('display');
}
async function selectMiddlePeak(app) {
  await app.$('plot').dispatch('click', {clientX: 240, clientY: 304});
}
function labelOnCanvas(app, label) {
  const text = app.$('plot').context.texts.find(entry => entry.text === label);
  assert.ok(text, `saved label ${label} is drawn`);
  return text;
}
async function clickLabel(app, label) {
  const {x, y} = labelOnCanvas(app, label);
  await app.$('plot').dispatch('click', {clientX: x + 2, clientY: y});
}

test('review label dialog retargets peaks and keyboard arrows; Cancel, Escape, and reopening clear the marker', async () => {
  const app = await launch(), {$, calls} = app;
  await openNewest(app);
  await selectMiddlePeak(app);
  assert.equal($('feature-dialog').hidden, false);
  assert.equal($('feature-action').textContent, 'Label');
  assert.match($('feature-title').textContent, /600\.0 nm/);
  assert.ok($('plot').context.arcs.length, 'selected feature has a marker');
  await $('plot').dispatch('click', {clientX: 78, clientY: 307});
  assert.equal($('feature-dialog').hidden, false);
  assert.match($('feature-title').textContent, /450\.0 nm/);
  assert.equal($('peak-label').textContent, '450.0 nm');
  await $('plot').dispatch('keydown', {key: 'ArrowRight'});
  assert.match($('feature-title').textContent, /600\.0 nm/);
  await app.click('feature-cancel');
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('peak-label').textContent, '');
  assert.equal($('plot').context.arcs.length, 0);
  await selectMiddlePeak(app);
  await $('feature-dialog').dispatch('keydown', {key: 'Escape'});
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('peak-label').textContent, '');
  await selectMiddlePeak(app);
  await app.click('display');
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('peak-label').textContent, '');
  assert.equal($('plot').context.arcs.length, 0);
  assert.ok(calls.every(call => call.method === 'GET'), 'selection and cancellation never save');
});

test('review label entry saves the sensor pixel and text, redraws labels across modes, and reloads saved labels', async () => {
  const app = await launch(), {$, records, calls} = app;
  const before = structuredClone(records.get(2000));
  await openNewest(app);
  await selectMiddlePeak(app);
  await app.click('feature-action');
  assert.equal($('name-dialog').open, true);
  assert.match($('name-title').textContent, /label/i);
  assert.equal($('name-label').textContent, 'Label');
  assert.equal($('name-input').value, '');
  await app.submit('  Hydrogen α  ');
  assert.equal($('name-dialog').open, false);
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('peak-label').textContent, '');
  assert.deepEqual(calls.at(-1), {method: 'PATCH', path: '/spectrum/2000',
    body: JSON.stringify({peak_label: {pixel: 4, label: 'Hydrogen α'}})});
  assert.deepEqual(records.get(2000), {...before, peak_labels: {4: 'Hydrogen α'}});
  labelOnCanvas(app, 'Hydrogen α');
  await app.dom.querySelectorAll('[data-channel]')[0].dispatch('click');
  labelOnCanvas(app, 'Hydrogen α');
  await app.click('emission-toggle');
  labelOnCanvas(app, 'Hydrogen α');
  await app.click('display');
  labelOnCanvas(app, 'Hydrogen α');
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('record-name').textContent, 'New lamp');
});

test('valley labels remain selectable for deletion when displayed in emission mode', async () => {
  const app = await launch(), {$, records, calls} = app;
  await openNewest(app);
  await app.click('emission-toggle');
  await $('plot').dispatch('click', {clientX: 132, clientY: 323});
  assert.match($('feature-title').textContent, /500\.0 nm/);
  await app.click('feature-action');
  await app.submit('Absorption line');
  assert.deepEqual(records.get(2000).peak_labels, {6: 'Absorption line'});
  await app.click('emission-toggle');
  for (const button of app.dom.querySelectorAll('[data-channel]')) await button.dispatch('click');
  assert.equal($('channel-label').textContent, 'All channels off');
  await clickLabel(app, 'Absorption line');
  assert.equal($('feature-action').textContent, 'Delete');
  assert.match($('feature-title').textContent, /Absorption line/);
  assert.equal($('peak-label').textContent, '500.0 nm');
  const requestCount = calls.length;
  await app.click('feature-cancel');
  assert.equal(calls.length, requestCount);
  assert.deepEqual(records.get(2000).peak_labels, {6: 'Absorption line'});
  await clickLabel(app, 'Absorption line');
  await app.click('feature-action');
  assert.deepEqual(calls.at(-1), {method: 'PATCH', path: '/spectrum/2000',
    body: JSON.stringify({peak_label: {pixel: 6, label: null}})});
  assert.deepEqual(records.get(2000).peak_labels, {});
  assert.equal($('feature-dialog').hidden, true);
  assert.equal($('peak-label').textContent, '');
  assert.ok(!$('plot').context.texts.some(entry => entry.text === 'Absorption line'));
  assert.equal($('spectrum-view').hidden, false, 'deleting a label preserves the spectrum');
  assert.ok(calls.every(call => call.method !== 'DELETE'));
});

test('annotation keyboard cancellation and Escape discard text and clear the marker without saving', async () => {
  const app = await launch(), {$, calls} = app;
  await openNewest(app);
  for (const cancel of [() => app.click('name-cancel'), () => $('name-dialog').dispatch('cancel')]) {
    await selectMiddlePeak(app);
    await app.click('feature-action');
    $('name-input').value = 'Unsaved line';
    await cancel();
    assert.equal($('name-dialog').open, false);
    assert.equal($('feature-dialog').hidden, true);
    assert.equal($('peak-label').textContent, '');
    assert.equal($('plot').context.arcs.length, 0);
  }
  assert.ok(calls.every(call => call.method === 'GET'));
  await app.click('rename');
  assert.equal($('name-label').textContent, 'Name');
  assert.equal($('name-input').value, 'New lamp');
});

test('failed label saves preserve text and existing labels, validate input, and allow retry', async () => {
  const app = await launch(), {$, records, calls, overrides} = app;
  records.get(2000).peak_labels = {1: 'Existing label'};
  await openNewest(app);
  await selectMiddlePeak(app);
  await app.click('feature-action');
  const initialCount = calls.length;
  await app.submit('   ');
  await app.submit('x'.repeat(65));
  assert.equal(calls.length, initialCount);
  assert.match($('name-error').textContent, /1–64/);
  overrides.set('PATCH /spectrum/2000', response(500, {error: 'Cannot save spectrum label'}));
  await app.submit('Retry label');
  assert.equal($('name-dialog').open, true);
  assert.equal($('name-input').value, 'Retry label');
  assert.equal($('name-input').disabled, false);
  assert.match($('name-error').textContent, /Cannot save spectrum label/);
  assert.deepEqual(records.get(2000).peak_labels, {1: 'Existing label'});
  labelOnCanvas(app, 'Existing label');
  assert.ok(!$('plot').context.texts.some(entry => entry.text === 'Retry label'));
  overrides.delete('PATCH /spectrum/2000');
  await app.submit('Retry label');
  assert.deepEqual(records.get(2000).peak_labels, {1: 'Existing label', 4: 'Retry label'});
  labelOnCanvas(app, 'Existing label');
  labelOnCanvas(app, 'Retry label');
});

test('pending annotation saves block duplicate submissions, retargeting, mode changes, and cancellation', async () => {
  const app = await launch(), {$, calls, overrides} = app;
  await openNewest(app);
  await selectMiddlePeak(app);
  await app.click('feature-action');
  let complete;
  overrides.set('PATCH /spectrum/2000', () => new Promise(resolve => { complete = resolve; }));
  const pending = app.submit('Pending label');
  assert.equal($('review-view').getAttribute('aria-busy'), 'true');
  assert.equal($('name-input').disabled, true);
  assert.equal($('name-cancel').disabled, true);
  const requests = calls.length;
  await app.submit('Pending label');
  await $('plot').dispatch('click', {clientX: 78, clientY: 307});
  await $('plot').dispatch('keydown', {key: 'ArrowRight'});
  await app.click('emission-toggle');
  await app.click('display');
  await app.click('name-cancel');
  const escape = await $('name-dialog').dispatch('cancel');
  assert.equal(escape.defaultPrevented, true);
  assert.equal($('name-dialog').open, true);
  assert.equal($('peak-label').textContent, '600.0 nm');
  assert.equal($('mode-label').textContent, 'Emission');
  assert.equal(calls.length, requests);
  complete(response(200, {id: 2000, peak_labels: {4: 'Pending label'}}));
  await pending;
  assert.equal($('review-view').getAttribute('aria-busy'), 'false');
  assert.equal($('name-dialog').open, false);
  labelOnCanvas(app, 'Pending label');
});

test('failed label deletion preserves annotations and offers retry; retargeting resets deletion errors', async () => {
  const app = await launch(), {$, records, overrides, calls} = app;
  records.get(2000).peak_labels = {1: 'First line', 4: 'Middle line'};
  await openNewest(app);
  await clickLabel(app, 'Middle line');
  overrides.set('PATCH /spectrum/2000', response(500, {error: 'Cannot delete spectrum label'}));
  await app.click('feature-action');
  assert.equal($('feature-dialog').hidden, false);
  assert.equal($('feature-action').textContent, 'Delete');
  assert.equal($('feature-action').disabled, false);
  assert.match($('feature-error').textContent, /Cannot delete spectrum label/);
  labelOnCanvas(app, 'Middle line');
  assert.deepEqual(records.get(2000).peak_labels, {1: 'First line', 4: 'Middle line'});
  await $('plot').dispatch('click', {clientX: 78, clientY: 307});
  assert.equal($('feature-action').textContent, 'Label');
  assert.equal($('feature-error').textContent, '');
  await clickLabel(app, 'Middle line');
  overrides.delete('PATCH /spectrum/2000');
  await app.click('feature-action');
  assert.deepEqual(records.get(2000).peak_labels, {1: 'First line'});
  labelOnCanvas(app, 'First line');
  assert.deepEqual(JSON.parse(calls.at(-1).body), {peak_label: {pixel: 4, label: null}});
});
