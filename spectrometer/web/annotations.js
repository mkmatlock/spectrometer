// Stored annotations use sensor pixel coordinates, independent of plot modes.
const COLOR = '#ffd166';
const BACKGROUND = '#1b2632';

export function normalizePeakLabels(labels, roi) {
  const result = {};
  if (!labels || typeof labels !== 'object' || Array.isArray(labels)
      || !roi || !Number.isSafeInteger(roi[0]) || !Number.isSafeInteger(roi[2])) return result;
  for (const [key, label] of Object.entries(labels)) {
    const pixel = Number(key);
    if (!Number.isSafeInteger(pixel) || String(pixel) !== key || pixel < roi[0] || pixel >= roi[2]
        || typeof label !== 'string' || !label.trim() || [...label].length > 64) continue;
    result[key] = label;
  }
  return result;
}

export function hitTestPeakLabel(boxes, x, y) {
  // The last drawn box is on top when a densely annotated graph needs overlap.
  for (let i = boxes.length - 1; i >= 0; i--) {
    const box = boxes[i];
    if (x >= box.x && x <= box.x + box.width && y >= box.y && y <= box.y + box.height) return box;
  }
  return null;
}

function fitText(ctx, text, width) {
  if (ctx.measureText(text).width <= width) return text;
  const suffix = '…';
  if (ctx.measureText(suffix).width > width) return '';
  const characters = [...text];
  while (characters.length && ctx.measureText(characters.join('') + suffix).width > width) characters.pop();
  return characters.join('') + suffix;
}

function overlapArea(a, b) {
  return Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x))
    * Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
}

export function drawPeakLabels(ctx, bounds, intensity, roi, labels) {
  const boxes = [];
  if (!bounds || !intensity?.length) return boxes;
  const {left, right, top, bottom} = bounds;
  const width = right - left, height = bottom - top;
  if (![left, right, top, bottom].every(Number.isFinite) || width <= 0 || height <= 0) return boxes;
  const normalized = normalizePeakLabels(labels, roi);
  const padding = 4, boxHeight = Math.min(28, height);
  const textWidth = Math.max(0, Math.min(180, width - padding * 2));
  const annotations = [];
  ctx.save();
  try {
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.lineWidth = 1;
    for (const key of Object.keys(normalized).sort((a, b) => Number(a) - Number(b))) {
      const pixel = Number(key), index = pixel - roi[0];
      if (index >= intensity.length || !Number.isFinite(intensity[index])) continue;
      const peakX = bounds.x(pixel), peakY = bounds.y(intensity[index]);
      if (!Number.isFinite(peakX) || !Number.isFinite(peakY)) continue;
      const x = Math.max(left, Math.min(right, peakX));
      const y = Math.max(top, Math.min(bottom, peakY));
      const text = fitText(ctx, normalized[key], textWidth);
      const boxWidth = Math.min(width, Math.max(28, ctx.measureText(text).width + padding * 2));
      let best = null, leastOverlap = Infinity;
      const rows = Math.max(1, Math.floor((height - boxHeight) / (boxHeight + 2)) + 1);
      for (let row = 0; row < rows; row++) {
        const candidate = {pixel, x: Math.max(left, Math.min(right - boxWidth, x - boxWidth / 2)),
          y: top + row * (boxHeight + 2), width: boxWidth, height: boxHeight};
        const overlap = boxes.reduce((sum, box) => sum + overlapArea(candidate, box), 0);
        if (overlap < leastOverlap) { best = candidate; leastOverlap = overlap; }
        if (!overlap) break;
      }
      boxes.push(best);
      annotations.push({box: best, text, x, y});
    }
    ctx.beginPath(); ctx.rect(left, top, width, height); ctx.clip();
    ctx.strokeStyle = COLOR;
    // Draw leaders first so they cannot cross another annotation's text.
    for (const {box, x, y} of annotations) {
      const targetY = y >= box.y + box.height / 2 ? box.y + box.height : box.y;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(box.x + box.width / 2, targetY); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.stroke();
    }
    for (const {box, text} of annotations) {
      ctx.fillStyle = BACKGROUND; ctx.fillRect(box.x, box.y, box.width, box.height);
      ctx.strokeRect(box.x + .5, box.y + .5, Math.max(0, box.width - 1), Math.max(0, box.height - 1));
      ctx.fillStyle = COLOR; ctx.fillText(text, box.x + box.width / 2, box.y + box.height / 2);
    }
  } finally { ctx.restore(); }
  return boxes;
}
