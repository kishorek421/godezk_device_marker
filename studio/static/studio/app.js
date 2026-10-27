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
document.querySelectorAll('form[data-api]').forEach(form => form.addEventListener('submit', async event => {
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
    if (result.redirect) location.href = result.redirect;
    else if (result.reload) location.reload();
    else notify('Saved successfully');
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}));
