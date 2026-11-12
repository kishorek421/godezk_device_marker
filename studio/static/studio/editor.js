(() => {
  const config = JSON.parse(document.getElementById('editor-data').textContent);
  const $ = id => document.getElementById(id);
  const svg = $('overlay');
  let doc = structuredClone(config.annotation);
  doc.regions ||= []; doc.relations ||= [];
  let selected = null, tool = 'select', start = null, draft = [], dirty = false, saving = false, blocked = false;
  let undo = [], redo = [], zoom = 1, generation = 0, drag = null, resize = null;
  let panning = false, panOrigin = null;
  const scroller = $('canvas-scroll');
  const storageKey = `godezk-marker:${config.id}`;
  const labels = new Map(config.labels.map(label => [label.id, label]));
  const status = message => $('save-status').textContent = message;
  function allowedType() {
    if (config.geometry && config.geometry !== 'any') return config.geometry;
    return doc.regions[0]?.type || null;
  }
  function syncTools() {
    const allowed = allowedType();
    document.querySelectorAll('[data-tool]').forEach(b => { b.style.display = allowed && !['select', allowed].includes(b.dataset.tool) ? 'none' : ''; });
    if (allowed && !['select', allowed].includes(tool)) {
      tool = 'select'; draft = []; start = null; svg.style.cursor = 'default';
      document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === 'select'));
    }
    const hint = $('editor-hint');
    if (hint) hint.textContent = config.geometry && config.geometry !== 'any' ? `This project is locked to ${config.geometry} annotations.` : allowed ? `Geometry locked to ${allowed} — delete all regions to change it.` : 'The first region drawn sets this project\'s geometry.';
  }
  try {
    const cached = JSON.parse(localStorage.getItem(storageKey));
    if (config.editable && cached?.revision === config.revision && JSON.stringify(cached.doc) !== JSON.stringify(doc) && confirm('Recover unsaved annotations from this browser?')) {
      doc = cached.doc; dirty = true; generation++;
      status('Recovered local changes · not yet saved');
    }
  } catch { notify('Browser recovery storage is unavailable.', true); }
  function cache() {
    try { localStorage.setItem(storageKey, JSON.stringify({revision: config.revision, doc})); }
    catch { notify('Local recovery storage is full or unavailable. Save before leaving.', true); }
  }
  function checkpoint() {
    undo.push(JSON.stringify(doc)); if (undo.length > 80) undo.shift(); redo = [];
  }
  function changed() { dirty = true; generation++; status('Unsaved changes'); cache(); render(); }
  function element(tag, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }
  function shape(region, preview = false) {
    const color = labels.get(region.label)?.color || '#ffffff';
    const points = region.points;
    const common = {fill: selected === region.id ? `${color}55` : `${color}22`, stroke: color, 'stroke-width': selected === region.id ? 5 : 2, 'vector-effect': 'non-scaling-stroke'};
    let node;
    if (region.type === 'box') node = element('rect', {...common, x: points[0][0] * 1000, y: points[0][1] * 1000, width: (points[1][0] - points[0][0]) * 1000, height: (points[1][1] - points[0][1]) * 1000});
    else if (region.type === 'point') node = element('ellipse', {...common, cx: points[0][0] * 1000, cy: points[0][1] * 1000, rx: 7, ry: 7});
    else node = element('polygon', {...common, points: points.map(p => p.map(n => n * 1000).join(',')).join(' ')});
    if (!preview) node.dataset.region = region.id;
    else node.style.pointerEvents = 'none';
    svg.appendChild(node);
    if (!preview) {
      const text = element('text', {x: points[0][0] * 1000 + 5, y: Math.max(18, points[0][1] * 1000 - 7), fill: color, 'font-size': 18, 'pointer-events': 'none'});
      text.textContent = labels.get(region.label)?.name || 'Unknown'; svg.appendChild(text);
    }
    if (!preview && config.editable && selected === region.id && region.type === 'box') {
      const xs = [points[0][0], points[1][0]], ys = [points[0][1], points[1][1]];
      [[0, 0], [1, 0], [1, 1], [0, 1]].forEach(([xi, yi], i) => {
        const handle = element('rect', {x: xs[xi] * 1000 - 7, y: ys[yi] * 1000 - 7, width: 14, height: 14, fill: '#ffffff', stroke: color, 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke'});
        handle.dataset.handle = i; handle.dataset.region = region.id;
        handle.style.cursor = i % 2 ? 'nesw-resize' : 'nwse-resize';
        svg.appendChild(handle);
      });
    }
  }
  function render() {
    svg.replaceChildren(); doc.regions.forEach(r => shape(r));
    if (draft.length) shape({label: $('label').value, type: 'polygon', points: draft}, true);
    $('region-count').textContent = doc.regions.length;
    $('regions').replaceChildren();
    doc.regions.forEach((r, i) => {
      const row = document.createElement('div'); row.className = 'region-row';
      const button = document.createElement('button'); button.className = `region-item ${selected === r.id ? 'selected' : ''}`;
      const dot = document.createElement('span'); dot.className = 'region-dot'; dot.style.background = labels.get(r.label)?.color || '#ffffff';
      button.appendChild(dot);
      button.appendChild(document.createTextNode(`${i + 1}. ${labels.get(r.label)?.name || 'Unknown'} · ${r.type}`));
      button.onclick = () => { selected = r.id; render(); };
      row.appendChild(button);
      if (config.editable) {
        const del = document.createElement('button'); del.className = 'region-del'; del.type = 'button'; del.textContent = '✕'; del.title = `Delete region ${i + 1}`;
        del.onclick = () => { checkpoint(); doc.regions = doc.regions.filter(x => x.id !== r.id); doc.relations = doc.relations.filter(x => x.from !== r.id && x.to !== r.id); if (selected === r.id) selected = null; changed(); };
        row.appendChild(del);
      }
      $('regions').appendChild(row);
    });
    const current = document.querySelector('.region-item.selected');
    if (current) current.scrollIntoView({block: 'nearest'});
    const attrs = $('attributes'); attrs.replaceChildren();
    const region = doc.regions.find(r => r.id === selected);
    if (region) {
      const heading = document.createElement('h3'); heading.textContent = 'Region attributes'; attrs.appendChild(heading);
      for (const attr of labels.get(region.label)?.attributes || []) {
        const def = typeof attr === 'string' ? {key: attr, options: null} : attr;
        const label = document.createElement('label'); label.textContent = def.key;
        let input;
        if (def.options?.length) {
          input = document.createElement('select');
          const empty = document.createElement('option'); empty.value = ''; empty.textContent = '—'; input.appendChild(empty);
          def.options.forEach(o => { const option = document.createElement('option'); option.value = o; option.textContent = o; input.appendChild(option); });
        } else {
          input = document.createElement('input'); input.maxLength = 1000;
        }
        input.value = region.attributes?.[def.key] || ''; input.disabled = !config.editable;
        input.onchange = () => { checkpoint(); region.attributes ||= {}; region.attributes[def.key] = input.value; changed(); };
        label.appendChild(input); attrs.appendChild(label);
      }
    }
    for (const id of ['rel-from', 'rel-to']) {
      const select = $(id), previous = select.value; select.replaceChildren();
      doc.regions.forEach((r, index) => { const option = document.createElement('option'); option.value = r.id; option.textContent = `${index + 1}. ${labels.get(r.label)?.name}`; select.appendChild(option); });
      if (doc.regions.some(r => r.id === previous)) select.value = previous;
    }
    $('relations').replaceChildren();
    doc.relations.forEach((r, index) => {
      const row = document.createElement('div'); row.className = 'relation';
      const number = id => doc.regions.findIndex(region => region.id === id) + 1;
      const text = document.createElement('span'); text.textContent = `${number(r.from)} → ${r.key} → ${number(r.to)}`; row.appendChild(text);
      if (config.editable) { const button = document.createElement('button'); button.className = 'ghost'; button.textContent = 'Remove'; button.onclick = () => { checkpoint(); doc.relations.splice(index, 1); changed(); }; row.appendChild(button); }
      $('relations').appendChild(row);
    });
    $('delete-region').disabled = !config.editable || !region;
    $('undo').disabled = !config.editable || !undo.length;
    $('redo').disabled = !config.editable || !redo.length;
    $('link').disabled = !config.editable;
    syncTools();
  }
  function position(event) {
    const rect = svg.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];
  }
  function add(type, points) {
    if (allowedType() && type !== allowedType()) return;
    if (!$('label').value) return notify('Create a label key in the project first.', true);
    checkpoint(); const r = {id: crypto.randomUUID(), label: $('label').value, type, points, attributes: {}};
    doc.regions.push(r); selected = r.id; setTool('select'); changed();
  }
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    if (panning) { panOrigin = {x: event.clientX, y: event.clientY, left: scroller.scrollLeft, top: scroller.scrollTop}; svg.setPointerCapture(event.pointerId); return; }
    if (tool === 'select') {
      selected = event.target.dataset.region || null;
      if (selected && config.editable) {
        checkpoint();
        const region = doc.regions.find(r => r.id === selected);
        if (event.target.dataset.handle !== undefined) resize = {corner: +event.target.dataset.handle, original: structuredClone(region.points)};
        else drag = {point: position(event), original: structuredClone(region.points)};
        svg.setPointerCapture(event.pointerId);
      }
      render(); return;
    }
    if (!config.editable) return;
    if (tool === 'box') { start = position(event); svg.setPointerCapture(event.pointerId); }
  });
  svg.addEventListener('pointermove', event => {
    if (panOrigin) { scroller.scrollLeft = panOrigin.left - (event.clientX - panOrigin.x); scroller.scrollTop = panOrigin.top - (event.clientY - panOrigin.y); return; }
    if (resize) {
      const p = position(event), region = doc.regions.find(r => r.id === selected);
      const xs = [resize.original[0][0], resize.original[1][0]], ys = [resize.original[0][1], resize.original[1][1]];
      xs[resize.corner === 1 || resize.corner === 2 ? 1 : 0] = p[0];
      ys[resize.corner >= 2 ? 1 : 0] = p[1];
      region.points = [[Math.min(xs[0], xs[1]), Math.min(ys[0], ys[1])], [Math.max(xs[0], xs[1]), Math.max(ys[0], ys[1])]];
      render(); return;
    }
    if (drag) {
      const now = position(event), xs = drag.original.map(p => p[0]), ys = drag.original.map(p => p[1]);
      const dx = Math.max(-Math.min(...xs), Math.min(1 - Math.max(...xs), now[0] - drag.point[0]));
      const dy = Math.max(-Math.min(...ys), Math.min(1 - Math.max(...ys), now[1] - drag.point[1]));
      doc.regions.find(r => r.id === selected).points = drag.original.map(p => [p[0] + dx, p[1] + dy]);
      render();
    } else if (start) {
      render(); const p = position(event);
      shape({label: $('label').value, type: 'box', points: [[Math.min(start[0], p[0]), Math.min(start[1], p[1])], [Math.max(start[0], p[0]), Math.max(start[1], p[1])]]}, true);
    }
  });
  svg.addEventListener('pointerup', event => {
    panOrigin = null;
    if (resize) {
      const region = doc.regions.find(r => r.id === selected), was = resize; resize = null;
      if (Math.abs(region.points[1][0] - region.points[0][0]) < .002 || Math.abs(region.points[1][1] - region.points[0][1]) < .002) region.points = was.original;
      if (JSON.stringify(region.points) === JSON.stringify(was.original)) render(); else changed();
    }
    if (drag) { drag = null; changed(); }
    if (!start) return;
    const p = position(event), first = start; start = null;
    if (Math.abs(p[0] - first[0]) < .002 || Math.abs(p[1] - first[1]) < .002) return render();
    add('box', [[Math.min(first[0], p[0]), Math.min(first[1], p[1])], [Math.max(first[0], p[0]), Math.max(first[1], p[1])]]);
  });
  svg.addEventListener('pointercancel', () => { start = null; panOrigin = null; if (resize) { doc.regions.find(r => r.id === selected).points = resize.original; resize = null; } if (drag) { doc.regions.find(r => r.id === selected).points = drag.original; drag = null; } render(); });
  svg.addEventListener('click', event => {
    if (!config.editable) return;
    if (tool === 'point') add('point', [position(event)]);
    if (tool === 'polygon' && event.detail === 1) { draft.push(position(event)); render(); }
  });
  function finishPolygon() { if (draft.length >= 3) { const points = draft; draft = []; add('polygon', points); } }
  svg.addEventListener('dblclick', event => { event.preventDefault(); if (tool === 'polygon') finishPolygon(); });
  function setTool(value) {
    if (allowedType() && !['select', allowedType()].includes(value)) return;
    tool = value; draft = []; start = null; document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === value)); svg.style.cursor = value === 'select' ? 'default' : 'crosshair'; render();
  }
  document.querySelectorAll('[data-tool]').forEach(b => b.onclick = () => setTool(b.dataset.tool));
  $('delete-region').onclick = () => {
    if (!config.editable || !selected) return;
    checkpoint(); doc.regions = doc.regions.filter(r => r.id !== selected); doc.relations = doc.relations.filter(r => r.from !== selected && r.to !== selected); selected = null; changed();
  };
  $('undo').onclick = () => { if (!undo.length || !config.editable) return; redo.push(JSON.stringify(doc)); doc = JSON.parse(undo.pop()); selected = null; changed(); };
  $('redo').onclick = () => { if (!redo.length || !config.editable) return; undo.push(JSON.stringify(doc)); doc = JSON.parse(redo.pop()); selected = null; changed(); };
  $('link').onclick = () => {
    const from = $('rel-from').value, to = $('rel-to').value, key = $('rel-key').value.trim();
    if (!config.editable) return;
    if (!from || !to || from === to || !/^[a-zA-Z0-9_-]{1,64}$/.test(key)) return notify('Choose two distinct regions and a valid relationship key.', true);
    checkpoint(); doc.relations.push({from, to, key}); changed();
  };
  function setZoom(value) { zoom = Math.max(1, Math.min(4, value)); $('stage').style.width = `${zoom * 100}%`; $('zoom-level').textContent = `${Math.round(zoom * 100)}%`; }
  $('zoom-in').onclick = () => setZoom(zoom + .25); $('zoom-out').onclick = () => setZoom(zoom - .25);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function save(submit = false) {
    while (saving) await sleep(50);
    if (blocked || !config.editable) return false;
    if (draft.length) { notify('Finish the polygon with Enter, or cancel with Escape.', true); return false; }
    if (!dirty && !submit) return true;
    saving = true; const sentGeneration = generation; status('Saving…');
    if ($('submit-next')) $('submit-next').disabled = true;
    try {
      const result = await apiRequest(`/tasks/${config.id}/save/`, {revision: config.revision, annotation: structuredClone(doc), submit});
      config.revision = result.revision;
      if (sentGeneration === generation) { dirty = false; try { localStorage.removeItem(storageKey); } catch {} }
      else cache();
      status(dirty ? 'Unsaved changes' : `Saved · revision ${result.revision}`);
      if (result.submitted) config.editable = false;
      return result;
    } catch (error) {
      status('Save failed · changes kept locally'); notify(error.message, true);
      if (error.message.startsWith('Conflict:')) blocked = true;
      return false;
    } finally { saving = false; if ($('submit-next')) $('submit-next').disabled = false; }
  }
  if ($('save')) $('save').onclick = () => save();
  if ($('submit-next')) $('submit-next').onclick = async () => {
    const issues = [];
    if (!doc.regions.length) issues.push('No regions drawn');
    doc.regions.forEach((r, i) => {
      const missing = (labels.get(r.label)?.attributes || []).map(a => typeof a === 'string' ? a : a.key).filter(k => !(r.attributes?.[k] || '').trim());
      if (missing.length) issues.push(`Region ${i + 1}: empty ${missing.join(', ')}`);
    });
    if (issues.length && !confirm(`Warnings:\n${issues.slice(0, 10).join('\n')}${issues.length > 10 ? '\n…' : ''}\n\nSubmit anyway?`)) return;
    $('submit-next').disabled = true;
    status('Submitting…');
    if (await save(true)) location.href = config.next_id ? `/tasks/${config.next_id}/` : `/projects/${config.project_id}/`;
    else $('submit-next').disabled = false;
  };
  // Warm the browser cache with the next task's image so advancing feels instant
  if (config.next_id) { const prefetch = new Image(); prefetch.src = `/tasks/${config.next_id}/asset/`; }
  setInterval(() => { if (dirty && !drag && !draft.length) save(); }, 5000);
  window.addEventListener('beforeunload', event => { if (dirty || saving) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('keydown', event => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    if (event.key === ' ') { event.preventDefault(); if (!panning) { panning = true; svg.style.cursor = 'grab'; } return; }
    if ((event.ctrlKey || event.metaKey) && event.key === 's') { event.preventDefault(); save(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key === 'z') { event.preventDefault(); (event.shiftKey ? $('redo') : $('undo')).click(); return; }
    if (event.key === 'Escape') { draft = []; start = null; render(); }
    if (event.key === 'Enter' && tool === 'polygon') finishPolygon();
    if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); $('delete-region').click(); }
    const shortcuts = {v: 'select', b: 'box', p: 'polygon', k: 'point'}; if (shortcuts[event.key]) setTool(shortcuts[event.key]);
  });
  document.addEventListener('keyup', event => {
    if (event.key === ' ') { panning = false; panOrigin = null; svg.style.cursor = tool === 'select' ? 'default' : 'crosshair'; }
  });
  const sourceImage = $('source-image');
  sourceImage.addEventListener('error', () => {
    sourceImage.style.display = 'none';
    const missing = document.createElement('div');
    missing.className = 'image-missing';
    missing.textContent = 'Image file is missing from storage — this task cannot be annotated.';
    $('stage').appendChild(missing);
    notify('Image file is missing — pick another task.', true);
  });
  render();
})();
