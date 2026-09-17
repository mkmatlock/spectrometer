import {prepareSpectrum, buildDisplay, pixelToWavelength, axisTicks, featureIndices, fitBlackbody} from './spectrum-math.js';

const $ = id => document.getElementById(id);
const state = {entries: [], selected: null, id: null, prepared: null, display: null,
  channels: ['Red', 'Green', 'Blue'], absorption: false, blackbody: false,
  fit: null, peak: null, busy: false, activity: 'review', nameAction: null};
let plotBounds = null;
let resizePending = false;

function status(message = '', error = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
}
function controls() {
  document.querySelectorAll('[data-action], .record').forEach(button => { button.disabled = state.busy; });
  $('display').disabled = state.busy || state.selected === null;
  $('rename').disabled = state.busy || state.selected === null;
  $('back').disabled = state.busy || state.id === null;
  document.querySelectorAll('.mode-toggle').forEach(button => { button.disabled = state.busy || !state.display; });
  $('delete').disabled = state.busy || state.id === null;
  document.querySelectorAll('#name-dialog button, #name-input, #delete-dialog button').forEach(el => { el.disabled = state.busy; });
  $('review-view').setAttribute('aria-busy', String(state.busy));
}
async function run(action, message, errorTarget = null) {
  if (state.busy) return;
  state.busy = true;
  controls();
  status(message);
  try { await action(); status(); }
  catch (error) {
    status(error.message, true);
    if (errorTarget) $(errorTarget).textContent = error.message;
  } finally { state.busy = false; controls(); }
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 150000);
  try {
    const response = await fetch(path, {...options, cache: 'no-store', signal: controller.signal});
    const data = response.status === 204 ? null : await response.json();
    if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The device took too long to respond. Refresh the list before retrying a capture; it may still have saved.');
    if (error instanceof TypeError) throw new Error('Cannot reach the spectrometer. Check the connection and try again.');
    throw error;
  } finally { clearTimeout(timeout); }
}
function switchActivity(activity) {
  state.activity = activity;
  $('review-view').hidden = activity !== 'review';
  $('settings-view').hidden = activity !== 'settings';
  for (const name of ['review', 'settings']) {
    if (name === activity) $(`${name}-nav`).setAttribute('aria-current', 'page');
    else $(`${name}-nav`).removeAttribute('aria-current');
  }
  if (activity === 'review') requestAnimationFrame(draw);
}
function renderList() {
  const list = $('spectrum-list');
  list.replaceChildren();
  $('list-summary').textContent = `${state.entries.length} recording${state.entries.length === 1 ? '' : 's'} · newest first`;
  if (!state.entries.length) {
    const p = document.createElement('p');
    p.className = 'secondary'; p.textContent = 'No spectra saved. Use Capture to record one.'; list.append(p);
  }
  for (const entry of state.entries) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'record';
    button.classList.toggle('selected', entry.id === state.selected);
    button.setAttribute('aria-pressed', String(entry.id === state.selected));
    const name = document.createElement('span'); name.className = 'record-name'; name.textContent = entry.name;
    const date = document.createElement('span'); date.className = 'record-date'; date.textContent = entry.timestamp || '';
    button.append(name, date);
    button.addEventListener('click', () => {
      state.selected = entry.id; renderList(); controls();
      list.querySelector('.selected')?.focus({preventScroll: true});
    });
    button.addEventListener('dblclick', () => run(() => loadSpectrum(entry.id), 'Loading spectrum…'));
    list.append(button);
  }
  controls();
}
async function fetchList() {
  const entries = await api('/list');
  if (!Array.isArray(entries)) throw new Error('The device returned an invalid spectrum list.');
  state.entries = entries.filter(e => Number.isSafeInteger(e.id)).sort((a, b) => b.id - a.id);
  if (!state.entries.some(e => e.id === state.selected)) state.selected = null;
  renderList();
}
function clearSpectrum() {
  state.id = null; state.prepared = state.display = state.fit = null; state.peak = null;
  $('spectrum-view').hidden = true; $('empty-view').hidden = false;
  controls();
}
async function loadSpectrum(id) {
  const record = await api(`/spectrum/${id}`);
  const prepared = prepareSpectrum(record);
  state.prepared = prepared; state.id = id; state.selected = id;
  state.channels = ['Red', 'Green', 'Blue']; state.absorption = false; state.blackbody = false;
  state.peak = null; state.fit = null;
  $('record-name').textContent = record.name;
  const entry = state.entries.find(e => e.id === id);
  if (entry && !entry.timestamp) entry.timestamp = formatDate(record.timestamp);
  $('record-date').textContent = entry?.timestamp || formatDate(record.timestamp);
  $('empty-view').hidden = true; $('spectrum-view').hidden = false;
  switchActivity('review'); renderList(); updateDisplay();
  if (matchMedia('(max-width: 850px)').matches) $('spectrum-view').scrollIntoView({behavior: 'smooth', block: 'start'});
}
function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').slice(0, 19);
}
function updateDisplay() {
  if (!state.prepared) return;
  state.display = buildDisplay(state.prepared, state.channels);
  state.peak = null;
  state.fit = null;
  $('fit-error').hidden = true;
  $('temperature').textContent = '';
  if (state.blackbody) {
    try {
      state.fit = fitBlackbody(state.display.intensity, state.display.roi, state.display.scalePoints);
      $('temperature').textContent = `T: ${Math.round(state.fit.temperature).toLocaleString()} K`;
    } catch (error) {
      $('fit-error').textContent = error.message;
      $('fit-error').hidden = false;
    }
  }
  $('axis-label').textContent = state.display.scalePoints.length >= 2 ? 'Wavelength (nm)' : 'Pixel';
  $('channel-label').textContent = state.channels.length ? state.channels.join(' · ') : 'All channels off';
  $('mode-label').textContent = state.absorption ? 'Absorption' : 'Emission';
  $('feature-kind').textContent = state.absorption ? 'valley' : 'peak';
  $('plot').setAttribute('aria-label', `Spectrum intensity graph. Click or touch to select a ${state.absorption ? 'valley' : 'peak'}. Use left and right arrows to move between features.`);
  $('emission-text').textContent = state.absorption ? 'Absorption' : 'Emission';
  $('emission-toggle').setAttribute('aria-pressed', String(state.absorption));
  $('blackbody-toggle').setAttribute('aria-pressed', String(state.blackbody));
  document.querySelectorAll('[data-channel]').forEach(button => button.setAttribute('aria-pressed', String(state.channels.includes(button.dataset.channel))));
  updatePeakLabel(); draw();
}
function updatePeakLabel() {
  if (state.peak === null || !state.display) { $('peak-label').textContent = ''; return; }
  const wavelength = pixelToWavelength(state.peak, state.display.scalePoints);
  $('peak-label').textContent = wavelength === null ? `Pixel ${state.peak}` : `${wavelength.toFixed(1)} nm`;
}
function canvasContext(canvas) {
  const {width, height} = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return {ctx, width, height};
}
function draw() {
  if (!state.display || state.activity !== 'review') return;
  const {ctx, width, height} = canvasContext($('plot'));
  if (!width) return;
  const left = 24, right = width - 24, top = 16, bottom = height - 32;
  const {intensity, roi, maximum, scalePoints} = state.display;
  const count = intensity.length, max = Math.max(1, maximum);
  const x = pixel => right - (pixel - roi[0]) / Math.max(1, count - 1) * (right - left);
  const y = value => bottom - Math.max(0, Math.min(1, value / max)) * (bottom - top);
  plotBounds = {left, right, top, bottom, x, y};
  ctx.clearRect(0, 0, width, height);
  ctx.lineWidth = 1; ctx.strokeStyle = '#35485b';
  for (let i = 0; i <= 4; i++) {
    const lineY = top + (bottom - top) * i / 4;
    ctx.beginPath(); ctx.moveTo(left, lineY); ctx.lineTo(right, lineY); ctx.stroke();
  }
  ctx.font = '13px system-ui, sans-serif'; ctx.fillStyle = '#a9bacb'; ctx.textAlign = 'center';
  let ticks;
  if (scalePoints.length >= 2) ticks = axisTicks(scalePoints, roi[0], roi[2] - 1);
  else {
    ticks = []; const step = Math.max(1, Math.ceil((count / 6) / 100) * 100);
    for (let pixel = Math.ceil(roi[0] / step) * step; pixel < roi[2]; pixel += step) ticks.push([pixel, pixel]);
  }
  let lastLabelRight = -Infinity;
  for (const [pixel, value] of ticks.slice().sort((a, b) => x(a[0]) - x(b[0]))) {
    const xx = x(pixel); if (xx < left - .1 || xx > right + .1) continue;
    ctx.beginPath(); ctx.moveTo(xx, bottom); ctx.lineTo(xx, bottom + 5); ctx.stroke();
    const textWidth = ctx.measureText(String(value)).width;
    const labelX = Math.max(textWidth / 2 + 2, Math.min(width - textWidth / 2 - 2, xx));
    if (labelX - textWidth / 2 >= lastLabelRight + 6) {
      ctx.fillText(String(value), labelX, bottom + 21); lastLabelRight = labelX + textWidth / 2;
    }
  }
  ctx.save(); ctx.beginPath(); ctx.rect(left, top - 1, right - left, bottom - top + 2); ctx.clip();
  // Preserve narrow peaks and troughs when several sensor columns share a screen pixel.
  ctx.strokeStyle = '#67dbb9'; ctx.lineWidth = 1.4; ctx.beginPath();
  const bins = Math.min(count, Math.max(1, Math.round(right - left)));
  for (let bin = 0; bin < bins; bin++) {
    const start = Math.floor(bin * count / bins), end = Math.floor((bin + 1) * count / bins);
    let low = Infinity, high = -Infinity;
    for (let i = start; i < end; i++) { low = Math.min(low, intensity[i]); high = Math.max(high, intensity[i]); }
    const xx = x(roi[0] + (start + end - 1) / 2);
    if (!bin) ctx.moveTo(xx, y(low)); else ctx.lineTo(xx, y(low));
    ctx.lineTo(xx, y(high));
  }
  ctx.stroke();
  if (state.fit) {
    ctx.strokeStyle = '#ffd166'; ctx.lineWidth = 2; ctx.beginPath(); let connected = false;
    state.fit.intensity.forEach((value, i) => {
      if (!Number.isFinite(value)) { connected = false; return; }
      if (connected) ctx.lineTo(x(roi[0] + i), y(value)); else ctx.moveTo(x(roi[0] + i), y(value));
      connected = true;
    }); ctx.stroke();
  }
  if (state.peak !== null) {
    const xx = x(state.peak), yy = y(intensity[state.peak - roi[0]]);
    ctx.strokeStyle = '#ffd166'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xx, top); ctx.lineTo(xx, bottom); ctx.stroke();
    ctx.beginPath(); ctx.arc(xx, yy, 5, 0, 2 * Math.PI); ctx.stroke();
  }
  ctx.restore();
  const slice = $('slice'), display = state.display;
  slice.width = display.width; slice.height = display.height;
  const pixels = new Uint8ClampedArray(display.width * display.height * 4);
  for (let row = 0; row < display.height; row++) for (let col = 0; col < display.width; col++) {
    const src = (row * display.width + col) * 3, dest = (row * display.width + display.width - 1 - col) * 4;
    pixels[dest] = display.rgb[src]; pixels[dest + 1] = display.rgb[src + 1]; pixels[dest + 2] = display.rgb[src + 2]; pixels[dest + 3] = 255;
  }
  slice.getContext('2d').putImageData(new ImageData(pixels, display.width, display.height), 0, 0);
}
function selectFeature(event) {
  if (!state.display || !plotBounds) return;
  const box = $('plot').getBoundingClientRect();
  const px = event.clientX - box.left, py = event.clientY - box.top;
  const {left, right, top, bottom, x, y} = plotBounds;
  if (px < left || px > right || py < top || py > bottom) return;
  const {intensity, roi} = state.display;
  let best = Infinity; state.peak = null;
  for (const i of featureIndices(intensity, state.absorption)) {
    const distance = (px - x(i + roi[0])) ** 2 + (py - y(intensity[i])) ** 2;
    if (distance < best) { best = distance; state.peak = i + roi[0]; }
  }
  updatePeakLabel();
  if (state.peak === null) $('peak-label').textContent = state.absorption ? 'No valleys found' : 'No peaks found';
  draw();
}
function openName(action) {
  if (state.busy) return;
  const entry = state.entries.find(e => e.id === state.selected);
  if (action === 'rename' && !entry) return;
  state.nameAction = {action, id: entry?.id};
  $('name-title').textContent = action === 'rename' ? 'Rename spectrum' : 'Capture spectrum';
  $('name-help').textContent = action === 'rename' ? 'The recording and its collection date stay the same.' : 'The camera must be running on the device.';
  $('name-input').value = action === 'rename' ? entry.name : '';
  $('name-error').textContent = '';
  $('name-dialog').showModal(); $('name-input').focus(); $('name-input').select();
}
async function submitName(event) {
  event.preventDefault();
  const name = $('name-input').value.trim();
  if (!name || Array.from(name).length > 64) { $('name-error').textContent = 'Enter a name of 1–64 characters.'; return; }
  const {action, id} = state.nameAction;
  $('name-error').textContent = '';
  await run(async () => {
    if (action === 'rename') {
      await api(`/spectrum/${id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name})});
      const entry = state.entries.find(e => e.id === id); if (entry) entry.name = name;
      if (state.id === id) $('record-name').textContent = name;
      renderList(); $('name-dialog').close();
    } else {
      const captured = await api(`/capture?name=${encodeURIComponent(name)}`);
      if (!Number.isSafeInteger(captured)) throw new Error('The camera returned an invalid spectrum ID. Refresh the list before capturing again.');
      $('name-dialog').close();
      // Once saved, failures while viewing must not invite duplicate capture.
      state.entries.unshift({id: captured, name, timestamp: ''});
      state.selected = captured; renderList(); switchActivity('review');
      status('Capture saved. Loading spectrum…');
      await loadSpectrum(captured);
    }
  }, action === 'rename' ? 'Saving name…' : 'Capturing spectrum…', 'name-error');
}
function settingsGroup(title, rows) {
  const group = document.createElement('section'); group.className = 'settings-group';
  const h = document.createElement('h2'); h.textContent = title; group.append(h);
  const list = document.createElement('dl');
  for (const [label, value] of rows) {
    const row = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = value; row.append(dt, dd); list.append(row);
  } group.append(list); return group;
}
async function showSettings() {
  const settings = await api('/settings');
  const camera = settings.camera || {}, calibration = settings.calibration || {}, network = settings.network || {};
  const content = $('settings-content'); content.replaceChildren();
  content.append(settingsGroup('Camera', [
    ['Frame rate', camera.frame_rate == null ? 'Unavailable' : `${camera.frame_rate} fps`],
    ['Resolution', camera.resolution?.join(' × ') || 'Unavailable'],
    ['Exposure time', camera.exposure_us == null ? 'Automatic' : `${camera.exposure_us / 1000} ms`],
    ['Frame averaging', camera.frame_averaging == null ? 'Unavailable' : `${camera.frame_averaging} frames`],
  ]));
  content.append(settingsGroup('Network', [['IP address', network.ip_address || 'Unavailable'], ['Wi-Fi network', network.wifi_ssid || 'Unavailable']]));
  content.append(settingsGroup('Sensor calibration', [['Sensor area (x₀, y₀, x₁, y₁)', calibration.sensor_area?.join(', ') || 'Not set']]));
  const points = Object.entries(calibration.scale || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  content.append(settingsGroup('Scale calibration', points.length ? points.map(([pixel, nm]) => [`Pixel ${pixel}`, `${nm} nm`]) : [['Labeled peaks', 'None']]));
  content.append(settingsGroup('Channel calibration', ['Red', 'Green', 'Blue'].map(channel => [channel, calibration.channel_ranges?.[channel]?.join(' – ') || 'Full sensor range'])));
  switchActivity('settings');
}
$('refresh').onclick = () => run(fetchList, 'Loading recordings…');
$('display').onclick = () => run(() => loadSpectrum(state.selected), 'Loading spectrum…');
$('back').onclick = () => { clearSpectrum(); status(); $('spectrum-list').scrollIntoView({block: 'nearest'}); };
$('rename').onclick = () => openName('rename');
$('capture-nav').onclick = () => openName('capture');
$('review-nav').onclick = () => { switchActivity('review'); status(); };
$('settings-nav').onclick = () => run(showSettings, 'Loading settings…');
$('settings-back').onclick = () => { switchActivity('review'); status(); };
$('name-form').onsubmit = submitName;
$('name-cancel').onclick = () => $('name-dialog').close();
for (const dialog of [$('name-dialog'), $('delete-dialog')]) dialog.addEventListener('cancel', e => { if (state.busy) e.preventDefault(); });
document.querySelectorAll('[data-channel]').forEach(button => button.onclick = () => {
  const name = button.dataset.channel;
  const previous = state.channels;
  state.channels = ['Red', 'Green', 'Blue'].filter(c => c === name ? !state.channels.includes(c) : state.channels.includes(c));
  try { updateDisplay(); }
  catch (error) {
    state.channels = previous;
    updateDisplay();
    $('fit-error').textContent = error.message;
    $('fit-error').hidden = false;
  }
});
$('emission-toggle').onclick = () => { state.absorption = !state.absorption; updateDisplay(); };
$('blackbody-toggle').onclick = () => { state.blackbody = !state.blackbody; updateDisplay(); };
$('delete').onclick = () => { $('delete-name').textContent = $('record-name').textContent; $('delete-error').textContent = ''; $('delete-dialog').showModal(); };
$('delete-cancel').onclick = () => $('delete-dialog').close();
$('delete-confirm').onclick = () => run(async () => {
  const id = state.id;
  await api(`/spectrum/${id}`, {method: 'DELETE'});
  $('delete-dialog').close(); state.entries = state.entries.filter(e => e.id !== id);
  if (state.selected === id) state.selected = null;
  clearSpectrum(); renderList();
}, 'Deleting spectrum…', 'delete-error');
$('plot').addEventListener('click', selectFeature);
$('plot').addEventListener('keydown', event => {
  if (!state.display || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  event.preventDefault();
  const {intensity, roi} = state.display;
  const features = Array.from(featureIndices(intensity, state.absorption), i => i + roi[0]).reverse();
  if (!features.length) return;
  let index = features.indexOf(state.peak);
  index = index < 0 ? Math.floor(features.length / 2) : Math.max(0, Math.min(features.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
  state.peak = features[index]; updatePeakLabel(); draw();
});
new ResizeObserver(() => {
  if (!resizePending) { resizePending = true; requestAnimationFrame(() => { resizePending = false; draw(); }); }
}).observe($('spectrum-view'));
run(fetchList, 'Loading recordings…');
