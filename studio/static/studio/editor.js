(() => {
  const config = JSON.parse(document.getElementById('editor-data').textContent);
  const $ = id => document.getElementById(id);
  const svg = $('overlay');
  let doc = structuredClone(config.annotation);
  doc.regions ||= []; doc.relations ||= [];
  let selected = null, tool = 'select', start = null, draft = [], dirty = false, saving = false, blocked = false;
  let undo = [], redo = [], zoom = 1, generation = 0, drag = null;
  const storageKey = `godezk-marker:${config.id}`;
  const labels = new Map(config.labels.map(label => [label.id, label]));
  const status = message => $('save-status').textContent = message;
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
    const common = {fill: `${color}22`, stroke: color, 'stroke-width': selected === region.id ? 4 : 2, 'vector-effect': 'non-scaling-stroke'};
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
  }
  function render() {
    svg.replaceChildren(); doc.regions.forEach(r => shape(r));
    if (draft.length) shape({label: $('label').value, type: 'polygon', points: draft}, true);
    $('region-count').textContent = doc.regions.length;
    $('regions').replaceChildren();
    doc.regions.forEach((r, i) => {
      const button = document.createElement('button'); button.className = `region-item ${selected === r.id ? 'selected' : ''}`;
      button.textContent = `${i + 1}. ${labels.get(r.label)?.name || 'Unknown'} · ${r.type}`;
      button.onclick = () => { selected = r.id; render(); }; $('regions').appendChild(button);
    });
    const attrs = $('attributes'); attrs.replaceChildren();
    const region = doc.regions.find(r => r.id === selected);
    if (region) {
      const heading = document.createElement('h3'); heading.textContent = 'Region attributes'; attrs.appendChild(heading);
      for (const key of labels.get(region.label)?.attributes || []) {
        const label = document.createElement('label'); label.textContent = key;
        const input = document.createElement('input'); input.value = region.attributes?.[key] || ''; input.disabled = !config.editable; input.maxLength = 1000;
        input.onchange = () => { checkpoint(); region.attributes ||= {}; region.attributes[key] = input.value; changed(); };
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
  }
  function position(event) {
    const rect = svg.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];
  }
  function add(type, points) {
    if (!$('label').value) return notify('Create a label key in the project first.', true);
    checkpoint(); const r = {id: crypto.randomUUID(), label: $('label').value, type, points, attributes: {}};
    doc.regions.push(r); selected = r.id; changed();
  }
  svg.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    if (tool === 'select') {
      selected = event.target.dataset.region || null;
      if (selected && config.editable) { checkpoint(); drag = {point: position(event), original: structuredClone(doc.regions.find(r => r.id === selected).points)}; svg.setPointerCapture(event.pointerId); }
      render(); return;
    }
    if (!config.editable) return;
    if (tool === 'box') { start = position(event); svg.setPointerCapture(event.pointerId); }
  });
  svg.addEventListener('pointermove', event => {
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
    if (drag) { drag = null; changed(); }
    if (!start) return;
    const p = position(event), first = start; start = null;
    if (Math.abs(p[0] - first[0]) < .002 || Math.abs(p[1] - first[1]) < .002) return render();
    add('box', [[Math.min(first[0], p[0]), Math.min(first[1], p[1])], [Math.max(first[0], p[0]), Math.max(first[1], p[1])]]);
  });
  svg.addEventListener('pointercancel', () => { start = null; if (drag) { doc.regions.find(r => r.id === selected).points = drag.original; drag = null; } render(); });
  svg.addEventListener('click', event => {
    if (!config.editable) return;
    if (tool === 'point') add('point', [position(event)]);
    if (tool === 'polygon' && event.detail === 1) { draft.push(position(event)); render(); }
  });
  function finishPolygon() { if (draft.length >= 3) { const points = draft; draft = []; add('polygon', points); } }
  svg.addEventListener('dblclick', event => { event.preventDefault(); if (tool === 'polygon') finishPolygon(); });
  function setTool(value) { tool = value; draft = []; start = null; document.querySelectorAll('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === value)); svg.style.cursor = value === 'select' ? 'default' : 'crosshair'; render(); }
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
  async function save(submit = false) {
    if (saving || blocked || !config.editable || (!dirty && !submit)) return;
    if (draft.length) return notify('Finish the polygon with Enter, or cancel with Escape.', true);
    saving = true; const sentGeneration = generation; status('Saving…');
    if ($('submit')) $('submit').disabled = true;
    try {
      const result = await apiRequest(`/tasks/${config.id}/save/`, {revision: config.revision, annotation: structuredClone(doc), submit});
      config.revision = result.revision;
      if (sentGeneration === generation) { dirty = false; try { localStorage.removeItem(storageKey); } catch {} }
      else cache();
      status(dirty ? 'Unsaved changes' : `Saved · revision ${result.revision}`);
      if (result.submitted) { config.editable = false; location.reload(); }
    } catch (error) {
      status('Save failed · changes kept locally'); notify(error.message, true);
      if (error.message.startsWith('Conflict:')) blocked = true;
    } finally { saving = false; if ($('submit')) $('submit').disabled = false; }
  }
  if ($('save')) $('save').onclick = () => save();
  if ($('submit')) $('submit').onclick = () => { if (confirm('Submit this annotation for independent review?')) save(true); };
  setInterval(() => { if (dirty && !drag && !draft.length) save(); }, 5000);
  window.addEventListener('beforeunload', event => { if (dirty || saving) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('keydown', event => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
    if ((event.ctrlKey || event.metaKey) && event.key === 's') { event.preventDefault(); save(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key === 'z') { event.preventDefault(); (event.shiftKey ? $('redo') : $('undo')).click(); return; }
    if (event.key === 'Escape') { draft = []; start = null; render(); }
    if (event.key === 'Enter' && tool === 'polygon') finishPolygon();
    if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); $('delete-region').click(); }
    const shortcuts = {v: 'select', b: 'box', p: 'polygon', k: 'point'}; if (shortcuts[event.key]) setTool(shortcuts[event.key]);
  });
  render();
})();
