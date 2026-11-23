/* The workbench page behaviour.
 *
 * Two rules that are not negotiable, because this page is shown to a hospital chain's
 * executives and drives real switches:
 *
 *   1. Nothing is ever applied without an approver typed in, and the button to apply is
 *      only rendered after a preview. The gate exists on the server too; this is the
 *      second lock, not the only one.
 *   2. Every string that comes from a device, NetBox or the audit log is inserted with
 *      textContent, never innerHTML. A switch's description field is attacker-controlled
 *      in a real estate and this page must not be the way a script gets into a browser.
 */

const state = {
  devices: [],
  pending: null, // the staged change, if any
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------- escaping */

/** Build an element with text, never markup. The only safe way to render untrusted text. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/* ----------------------------------------------------------------- http */

async function api(path, options) {
  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch (err) {
    return { ok: false, headline: 'The workbench did not answer', detail: String(err) };
  }
  return payload;
}

async function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/* ---------------------------------------------------------------- banner */

function showBanner(message) {
  const banner = $('banner');
  if (!message) {
    banner.hidden = true;
    banner.textContent = '';
    return;
  }
  banner.textContent = message;
  banner.hidden = false;
}

function setFabricState(kind, label) {
  const pill = $('fabric-state');
  pill.className = `pill pill-${kind}`;
  pill.textContent = label;
}

/* ---------------------------------------------------------------- tabs */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('is-active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('is-active'));
    tab.classList.add('is-active');
    $(`panel-${tab.dataset.panel}`).classList.add('is-active');

    if (tab.dataset.panel === 'lifecycle') loadLifecycle();
    if (tab.dataset.panel === 'vendors') loadVendors();
    if (tab.dataset.panel === 'audit') loadAudit();
  });
});

/* --------------------------------------------------------------- fabric */

function stat(label, value, kind) {
  const wrap = el('div', 'stat');
  wrap.appendChild(el('span', 'stat-label', label));
  wrap.appendChild(el('span', `stat-value${kind ? ' ' + kind : ''}`, value));
  return wrap;
}

function renderFindings(card, findings) {
  if (!findings || findings.length === 0) return;
  const list = el('ul', 'findings');
  findings.forEach((finding) => {
    list.appendChild(el('li', null, finding.detail));
  });
  card.appendChild(list);
}

function renderDevice(device) {
  const verdict = device.verdict || {};
  const kindClass = verdict.state === 'compliant' ? 'ok' : verdict.state === 'notice' ? 'warn' : 'bad';
  const card = el('div', `card device-card ${kindClass}`);

  const head = el('div', 'device-head');
  const nameWrap = el('div');
  nameWrap.appendChild(el('div', 'device-name', device.device));
  nameWrap.appendChild(el('div', 'device-role', device.role || 'unknown role'));
  head.appendChild(nameWrap);
  card.appendChild(head);

  const verdictText = verdict.headline || 'unknown';
  const verdictClass =
    verdict.state === 'compliant' ? 'verdict-ok' : verdict.state === 'notice' ? 'verdict-warn' : 'verdict-bad';
  card.appendChild(el('div', `device-verdict ${verdictClass}`, verdictText));

  const intended = device.intended || {};
  const actual = device.actual || {};

  const rows = [
    ['Intended VLANs', (intended.vlans || []).join(', ') || 'none'],
    ['On the switch', (actual.vlans || []).join(', ') || 'none'],
    ['Reachable', actual.reachable ? 'yes' : 'no'],
  ];
  if (actual.os_version) rows.push(['Software', actual.os_version]);

  rows.forEach(([label, value]) => {
    const row = el('div', 'kv');
    row.appendChild(el('span', null, label));
    row.appendChild(el('span', null, value));
    card.appendChild(row);
  });

  renderFindings(card, device.findings);
  return card;
}

async function loadFabric(newVlan) {
  const query = newVlan ? `?new_vlan=${encodeURIComponent(newVlan)}` : '';
  const result = await api(`/api/overview${query}`);
  const grid = $('fabric-grid');
  const summary = $('fabric-summary');
  clear(grid);
  clear(summary);

  if (!result.ok) {
    setFabricState('bad', 'cannot read');
    showBanner(`${result.headline}${result.detail ? ' — ' + result.detail : ''}`);
    grid.appendChild(el('p', 'empty', 'No device state available.'));
    return;
  }

  showBanner('');
  const drifted = result.drifted_count || 0;
  const total = result.device_count || 0;
  setFabricState(drifted === 0 ? 'ok' : 'bad', drifted === 0 ? 'all compliant' : `${drifted} of ${total} drifted`);

  summary.appendChild(stat('Devices', total));
  summary.appendChild(stat('Drifted', drifted, drifted === 0 ? 'ok' : 'bad'));
  summary.appendChild(stat('Compliant', total - drifted, 'ok'));
  if (result.proposed_vlan) summary.appendChild(stat('Proposed VLAN', result.proposed_vlan, 'warn'));

  (result.devices || []).forEach((device) => grid.appendChild(renderDevice(device)));
}

/* --------------------------------------------------------------- change */

function renderDiff(container, diff) {
  const pre = el('pre', 'diff');
  (diff || '').split('\n').forEach((line) => {
    const span = document.createElement('span');
    span.className = line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
    span.textContent = line + '\n';
    pre.appendChild(span);
  });
  container.appendChild(pre);
}

function renderResult(node, result, { withApprove = false, action = 'approve' } = {}) {
  clear(node);
  node.hidden = false;

  const okClass = result.ok === false ? 'result-bad' : result.no_drift ? 'result-warn' : 'result-ok';
  node.appendChild(el('div', `result-head ${okClass}`, result.headline || ''));
  if (result.detail) node.appendChild(el('p', 'subtle', result.detail));
  if (result.diff) renderDiff(node, result.diff);

  if (result.applied) {
    node.appendChild(el('p', 'subtle', `Recorded against ${result.approver}. See the audit trail.`));
  }

  if (withApprove && result.ok && !result.no_drift && !result.applied) {
    const row = el('div', 'approve-row');
    const field = el('label', 'field');
    field.appendChild(el('span', null, 'Approved by'));
    const input = document.createElement('input');
    input.type = 'text';
    input.id = `approver-${action}`;
    input.placeholder = 'your name';
    input.autocomplete = 'off';
    field.appendChild(input);
    row.appendChild(field);

    const button = el('button', 'btn', action === 'remediate' ? 'Approve and remediate' : 'Approve and apply');
    button.type = 'button';
    button.addEventListener('click', () => {
      const approver = input.value.trim();
      if (!approver) {
        input.focus();
        input.setAttribute('aria-invalid', 'true');
        return;
      }
      if (action === 'remediate') {
        doRemediate(approver);
      } else {
        doApprove(approver);
      }
    });
    row.appendChild(button);
    row.appendChild(
      el('p', 'approve-note', 'Nothing is sent to a switch until this is clicked with a name.')
    );
    node.appendChild(row);
  }
}

function selectedDevice() {
  return $('change-device').value;
}

function selectedVlan() {
  const value = parseInt($('change-vlan').value, 10);
  return Number.isFinite(value) ? value : 150;
}

async function doPreview() {
  const node = $('change-result');
  node.hidden = false;
  clear(node);
  node.appendChild(el('p', 'subtle', 'Previewing on the switch…'));

  const result = await post('/api/propose', { device: selectedDevice(), vlan: selectedVlan() });
  state.pending = result.ok && result.change_id ? result : null;
  renderResult(node, result, { withApprove: true, action: 'approve' });
  if (result.changed_lines) {
    // The fabric view is about to be stale; that is the point of the next refresh.
    setFabricState('warn', 'change staged');
  }
}

async function doApprove(approver) {
  const node = $('change-result');
  clear(node);
  node.appendChild(el('p', 'subtle', 'Applying…'));

  const result = await post('/api/approve', {
    device: selectedDevice(),
    vlan: selectedVlan(),
    approver,
  });
  renderResult(node, result);
  state.pending = null;
  await loadFabric();
}

async function doInject() {
  const node = $('drift-result');
  clear(node);
  node.hidden = false;
  node.appendChild(el('p', 'subtle', 'Breaking it on purpose…'));

  const result = await post('/api/inject', { device: selectedDevice(), vlan: selectedVlan() });
  renderResult(node, result);
  await loadFabric();
}

async function doRemediate(approver) {
  const node = $('drift-result');
  clear(node);
  node.appendChild(el('p', 'subtle', 'Remediating…'));

  const result = await post('/api/remediate', { device: selectedDevice(), approver });
  renderResult(node, result);
  await loadFabric();
}

async function doRemediatePreview() {
  const node = $('drift-result');
  node.hidden = false;
  clear(node);
  node.appendChild(el('p', 'subtle', 'Reading the fabric for drift…'));

  // Remediation previews through the same gate: ask the overview what is wrong first, so
  // the presenter can show the finding before approving the fix.
  const result = await api('/api/overview');
  if (!result.ok) {
    renderResult(node, result);
    return;
  }
  const device = result.devices.find((d) => d.device === selectedDevice());
  if (!device || (device.verdict || {}).state === 'compliant') {
    renderResult(node, {
      ok: true,
      no_drift: true,
      headline: `${selectedDevice()} matches intended state`,
      detail: 'inject drift first, then remediate it',
    });
    return;
  }
  renderResult(
    node,
    {
      ok: true,
      headline: device.verdict.headline,
      detail: `${device.findings.length} finding(s) on ${selectedDevice()}`,
      diff: (device.findings || []).map((f) => `- ${f.detail}`).join('\n'),
    },
    { withApprove: true, action: 'remediate' }
  );
}

/* ------------------------------------------------------------ lifecycle */

async function loadLifecycle() {
  const result = await api('/api/lifecycle');
  const summary = $('lifecycle-summary');
  const table = $('lifecycle-table');
  clear(summary);
  clear(table);

  if (!result.ok) {
    showBanner(`${result.headline}${result.detail ? ' — ' + result.detail : ''}`);
    table.appendChild(el('p', 'empty', 'No lifecycle data available.'));
    return;
  }
  showBanner('');

  summary.appendChild(stat('Devices', result.total));
  summary.appendChild(stat('Compliant', result.compliant, 'ok'));
  summary.appendChild(stat('Drifted', result.total - result.compliant, 'bad'));
  Object.entries(result.os_fleet || {}).forEach(([version, count]) => {
    summary.appendChild(stat(`OS ${version}`, count));
  });

  const tbl = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  ['Device', 'Role', 'State', 'Software', 'Target', 'Finding'].forEach((h) =>
    headRow.appendChild(el('th', null, h))
  );
  thead.appendChild(headRow);
  tbl.appendChild(thead);

  const tbody = document.createElement('tbody');
  (result.devices || []).forEach((device) => {
    const tr = document.createElement('tr');
    tr.appendChild(el('td', 'mono', device.device));
    tr.appendChild(el('td', null, device.role));
    const stateCell = el('td');
    const kind = device.state === 'compliant' ? 'ok' : device.state === 'notice' ? 'warn' : 'bad';
    stateCell.appendChild(el('span', `pill pill-${kind}`, device.state));
    tr.appendChild(stateCell);
    tr.appendChild(el('td', 'mono', device.os_version));
    tr.appendChild(el('td', 'mono', device.target_os));
    tr.appendChild(el('td', null, device.headline));
    tbody.appendChild(tr);
  });
  tbl.appendChild(tbody);
  table.appendChild(tbl);

  $('lifecycle-note').textContent = result.note || '';
}

/* ------------------------------------------------------------ two vendors */

async function loadVendors() {
  const result = await api('/api/two-vendors');
  const list = $('vendors-list');
  clear(list);

  if (!result.ok) {
    showBanner(`${result.headline}${result.detail ? ' — ' + result.detail : ''}`);
    list.appendChild(el('p', 'empty', 'No vendor comparison available.'));
    return;
  }
  showBanner('');

  (result.vendors || []).forEach((vendor) => {
    const kind = vendor.state === 'compliant' ? 'ok' : vendor.state === 'drifted' ? 'bad' : 'warn';
    const card = el('div', `card device-card ${kind}`);
    const head = el('div', 'device-head');
    const nameWrap = el('div');
    nameWrap.appendChild(el('div', 'device-name', vendor.device));
    nameWrap.appendChild(el('div', 'device-role', vendor.vendor));
    head.appendChild(nameWrap);
    card.appendChild(head);

    card.appendChild(
      el(
        'div',
        `device-verdict ${kind === 'ok' ? 'verdict-ok' : kind === 'bad' ? 'verdict-bad' : 'verdict-warn'}`,
        vendor.headline
      )
    );

    [
      ['Intent from', vendor.source],
      ['Reachable', vendor.reachable ? 'yes' : 'no'],
      ['What works here', vendor.capability],
    ].forEach(([label, value]) => {
      const row = el('div', 'kv');
      row.appendChild(el('span', null, label));
      row.appendChild(el('span', null, value));
      card.appendChild(row);
    });

    list.appendChild(card);
  });

  $('vendors-note').textContent = result.note || '';
}

/* ---------------------------------------------------------------- audit */

async function loadAudit() {
  const result = await api('/api/audit?limit=50');
  const summary = $('audit-summary');
  const list = $('audit-list');
  clear(summary);
  clear(list);

  if (!result.ok) {
    showBanner(`${result.headline}${result.detail ? ' — ' + result.detail : ''}`);
    list.appendChild(el('p', 'empty', 'No audit record available.'));
    return;
  }
  showBanner('');

  summary.appendChild(stat('Events', result.count));
  summary.appendChild(stat('Applied changes', result.applied, 'ok'));

  if (!result.entries || result.entries.length === 0) {
    list.appendChild(
      el('p', 'empty', 'Nothing recorded yet. Approve a change and it will appear here.')
    );
    return;
  }

  result.entries.forEach((entry) => {
    const row = el('div', `audit-row ${entry.kind || ''}`);
    const top = el('div', 'audit-top');
    top.appendChild(el('span', 'audit-kind', entry.kind || 'event'));
    top.appendChild(el('span', 'audit-when', entry.at || ''));
    row.appendChild(top);
    row.appendChild(el('div', 'audit-summary', entry.summary || ''));
    if (entry.approver) {
      row.appendChild(el('div', 'audit-approver', `Approved by ${entry.approver}`));
    }

    if (entry.diff_before || entry.diff_after) {
      const details = document.createElement('details');
      details.appendChild(el('summary', null, 'Show the before and after'));
      if (entry.diff_before) details.appendChild(el('p', 'subtle', 'Before'));
      if (entry.diff_before) renderDiff(details, entry.diff_before);
      if (entry.diff_after) details.appendChild(el('p', 'subtle', 'After'));
      if (entry.diff_after) renderDiff(details, entry.diff_after);
      row.appendChild(details);
    }
    list.appendChild(row);
  });
}

/* ----------------------------------------------------------------- init */

async function init() {
  const devices = await api('/api/devices');
  const select = $('change-device');
  clear(select);
  (devices.devices || []).forEach((device) => {
    const option = document.createElement('option');
    option.value = device;
    option.textContent = device;
    select.appendChild(option);
  });
  // Default to a leaf: the trunk story lives there.
  const leaf = (devices.devices || []).find((d) => d.startsWith('leaf'));
  if (leaf) select.value = leaf;

  $('refresh').addEventListener('click', () => loadFabric());
  $('change-preview').addEventListener('click', doPreview);
  $('inject').addEventListener('click', doInject);
  $('remediate').addEventListener('click', doRemediatePreview);
  $('lifecycle-refresh').addEventListener('click', loadLifecycle);
  $('vendors-refresh').addEventListener('click', loadVendors);
  $('audit-refresh').addEventListener('click', loadAudit);

  $('propose-preview').addEventListener('click', async () => {
    await loadFabric(selectedVlan());
    document.querySelector('.tab[data-panel="change"]').click();
  });

  api('/api/health').then((health) => {
    if (health && health.audit) $('footer-audit').textContent = `Audit record: ${health.audit}`;
  });

  await loadFabric();
}

document.addEventListener('DOMContentLoaded', init);
