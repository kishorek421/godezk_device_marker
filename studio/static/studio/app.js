window.notify = (message, error = false) => {
  const node = document.getElementById('notification');
  node.textContent = message;
  node.className = error ? 'visible error' : 'visible';
  clearTimeout(window.noticeTimer);
  window.noticeTimer = setTimeout(() => node.className = '', 8000);
};
window.apiRequest = async (url, body, json = true) => {
  const headers = {'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]')?.value || ''};
  if (json) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, {method: 'POST', headers, body: json ? JSON.stringify(body) : body});
  if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('Session expired or server unavailable. Reload and sign in.');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
};
// Pages restored from the back-forward cache show pre-navigation state
// (e.g. a just-created project missing). Reload to fetch fresh HTML.
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload();
});
document.querySelectorAll('form[data-api]:not([data-chunk])').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    let body = new FormData(form);
    if (form.hasAttribute('data-json')) {
      body = Object.fromEntries(body);
      if ('revision' in body) body.revision = Number(body.revision);
    }
    const result = await apiRequest(form.dataset.api, body, form.hasAttribute('data-json'));
    if (result.token) {
      const out = form.querySelector('[data-token-out]');
      if (out) out.innerHTML = `Copy now — shown once:<br><code>${result.token}</code>`;
      notify('API token created — copy it now');
    }
    else if (result.redirect) location.href = result.redirect;
    else if (result.reload) location.reload();
    else notify('Saved successfully');
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}));
document.querySelectorAll('form[data-chunk]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  const input = form.querySelector('input[type=file][name=images]') || form.querySelector('input[type=file]');
  const files = Array.from(input?.files || []);
  if (!files.length) return;
  const size = Number(form.dataset.chunk) || 30;
  const button = form.querySelector('button:not([type]), button[type=submit]');
  const progress = form.querySelector('[data-progress]');
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const assignee = form.querySelector('[name=assign_to]')?.value;
  button.disabled = true;
  const post = async list => {
    const body = new FormData();
    if (assignee) body.append('assign_to', assignee);
    list.forEach(f => body.append('images', f));
    const response = await fetch(form.dataset.api, {method: 'POST', headers: {'X-CSRFToken': csrf}, body});
    let data = {};
    try { data = await response.json(); } catch {}
    if (!response.ok) throw new Error(data.error || `Upload failed (${response.status})`);
    return data.count || list.length;
  };
  const failures = [];
  let done = 0;
  const update = () => { if (progress) progress.textContent = `${done} / ${files.length} uploaded`; };
  update();
  for (let i = 0; i < files.length; i += size) {
    const chunk = files.slice(i, i + size);
    try { done += await post(chunk); }
    catch {
      for (const f of chunk) {
        try { done += await post([f]); }
        catch (error) { failures.push(`${f.name}: ${error.message}`); }
        update();
      }
    }
    update();
  }
  button.disabled = false;
  if (failures.length) {
    if (progress) progress.textContent = `${done} of ${files.length} uploaded`;
    notify(`Rejected: ${failures.slice(0, 5).join(' · ')}${failures.length > 5 ? ` +${failures.length - 5} more` : ''} — reload to see uploaded tasks`, true);
  } else location.reload();
}));
