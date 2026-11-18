// indie recorder PWA: records with MediaRecorder in 30 s chunks, keeps every
// chunk in IndexedDB until the box confirms it, uploads over the VPN and
// resumes after network loss or an app restart.
'use strict';

const CHUNK_MS = 30000;
const $ = id => document.getElementById(id);

// ---- IndexedDB: recs (one per recording) and chunks ([client_id, idx]) ----

let dbp;
function db() {
  dbp = dbp || new Promise((resolve, reject) => {
    const r = indexedDB.open('indie-recorder', 1);
    r.onupgradeneeded = () => {
      r.result.createObjectStore('recs', { keyPath: 'client_id' });
      r.result.createObjectStore('chunks', { keyPath: ['client_id', 'idx'] });
    };
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  return dbp;
}
const wait = r => new Promise((res, rej) => { r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
const finished = t => new Promise((res, rej) => { t.oncomplete = () => res(); t.onerror = t.onabort = () => rej(t.error); });
async function put(store, value) { const t = (await db()).transaction(store, 'readwrite'); t.objectStore(store).put(value); return finished(t); }
async function del(store, key) { const t = (await db()).transaction(store, 'readwrite'); t.objectStore(store).delete(key); return finished(t); }
async function get(store, key) { return wait((await db()).transaction(store).objectStore(store).get(key)); }
async function all(store) { return wait((await db()).transaction(store).objectStore(store).getAll()); }
async function chunksOf(cid) {
  const range = IDBKeyRange.bound([cid, 0], [cid, Infinity]);
  return wait((await db()).transaction('chunks').objectStore('chunks').getAll(range));
}

// ---- server ----

const token = () => localStorage.getItem('ir_token') || '';
class AuthError extends Error {}

async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { Authorization: 'Bearer ' + token(), ...(opts.headers || {}) } });
  if (r.status === 401) throw new AuthError('token not accepted');
  return r;
}
const json = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');

// ---- recording ----

let mr = null, stream = null, active = null, nextIdx = 0, chain = Promise.resolve();
let startedAt = 0, clock = null, wakeLock = null, audioCtx = null, analyser = null;

async function start() {
  const voice = $('voiceproc').checked;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: voice, noiseSuppression: voice, autoGainControl: true },
  });
  const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
    .find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
  mr = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 48000 } : {});
  const rec = {
    client_id: crypto.randomUUID(), mime: mr.mimeType || mime || 'audio/webm',
    title: $('title').value.trim() || null, started_at: new Date().toISOString(),
    storage: $('drive').checked ? 'drive' : 'local',
    state: 'recording', next_idx: 0, recording_id: null, total_chunks: null,
  };
  await put('recs', rec);
  active = rec.client_id;
  nextIdx = 0;
  mr.ondataavailable = e => onChunk(rec.client_id, e.data);
  mr.onstop = () => onStopped(rec.client_id);
  mr.start(CHUNK_MS);
  startedAt = Date.now();
  clock = setInterval(tick, 500);
  tick();
  meter();
  await lockScreen();
  $('rec').classList.add('on');
  $('rec').textContent = 'Stop';
  $('title').disabled = true;
  $('drive').disabled = true;
  kick();
}

function onChunk(cid, blob) {
  if (!blob || !blob.size) return;
  const idx = nextIdx++;
  // A promise chain keeps the writes in order, so the stop handler runs after the last chunk is stored.
  chain = chain.then(async () => {
    await put('chunks', { client_id: cid, idx, blob, uploaded: false });
    const r = await get('recs', cid);
    r.next_idx = idx + 1;
    await put('recs', r);
    kick();
  });
}

function onStopped(cid) {
  chain = chain.then(async () => {
    const r = await get('recs', cid);
    r.state = 'stopped';
    r.total_chunks = r.next_idx;
    await put('recs', r);
    active = null;
    kick();
  });
}

function stop() {
  if (mr && mr.state !== 'inactive') mr.stop();
  if (stream) stream.getTracks().forEach(t => t.stop());
  clearInterval(clock);
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
  if (audioCtx) audioCtx.close();
  audioCtx = analyser = null;
  $('level').style.width = '0';
  $('warn').hidden = true;
  $('rec').classList.remove('on');
  $('rec').textContent = 'Record';
  $('title').disabled = false;
  $('drive').disabled = false;
  $('title').value = '';
}

function tick() {
  const s = Math.floor((Date.now() - startedAt) / 1000);
  $('clock').textContent = [s / 3600, (s % 3600) / 60, s % 60].map(v => String(Math.floor(v)).padStart(2, '0')).join(':');
}

function meter() {
  audioCtx = new AudioContext();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  audioCtx.createMediaStreamSource(stream).connect(analyser);
  const data = new Float32Array(analyser.fftSize);
  const draw = () => {
    if (!analyser) return;
    analyser.getFloatTimeDomainData(data);
    let peak = 0;
    for (const v of data) peak = Math.max(peak, Math.abs(v));
    $('level').style.width = Math.min(100, peak * 140) + '%';
    requestAnimationFrame(draw);
  };
  draw();
}

async function lockScreen() {
  if (!('wakeLock' in navigator)) {
    warn('This browser has no screen wake lock. Keep the screen on yourself while recording.');
    return;
  }
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch (e) {
    warn('Could not keep the screen on (' + e.message + '). Keep it on yourself while recording.');
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && mr && mr.state === 'recording' && !wakeLock) lockScreen();
});
window.addEventListener('beforeunload', e => { if (mr && mr.state === 'recording') { e.preventDefault(); e.returnValue = ''; } });

// ---- upload: one loop, retried with backoff, resumable at chunk level ----

let running = false, again = false, backoff = 0, retryTimer = null;

async function kick() {
  if (running) { again = true; return; }
  running = true;
  clearTimeout(retryTimer);
  try {
    do { again = false; await uploadAll(); } while (again);
    backoff = 0;
    $('net').textContent = '';
  } catch (e) {
    if (e instanceof AuthError) { showPair('The box did not accept this phone\'s token. Pair it again.'); return; }
    backoff = Math.min(30000, backoff ? backoff * 2 : 2000);  // VPN reconnects may not fire "online"
    $('net').textContent = (navigator.onLine ? 'Box not reachable' : 'Offline') +
      `, chunks stay on the phone. Retrying in ${Math.round(backoff / 1000)} s.`;
    retryTimer = setTimeout(kick, backoff);
  } finally {
    running = false;
    render();
  }
}

async function uploadAll() {
  const recs = (await all('recs')).sort((a, b) => a.started_at.localeCompare(b.started_at));
  for (const r of recs) {
    if (r.state !== 'finalized') await uploadRec(r);
  }
}

async function uploadRec(r) {
  if (!r.recording_id) {
    const res = await api('/api/v1/recordings', json({ client_id: r.client_id, mime: r.mime, title: r.title, started_at: r.started_at, storage: r.storage }));
    if (!res.ok) throw new Error('create ' + res.status);
    r.recording_id = (await res.json()).recording_id;
    await put('recs', r);
  }
  const todo = (await chunksOf(r.client_id)).filter(c => !c.uploaded).sort((a, b) => a.idx - b.idx);
  for (const c of todo) {
    const buf = await c.blob.arrayBuffer();
    const sha = hex(await crypto.subtle.digest('SHA-256', buf));
    const res = await api(`/api/v1/recordings/${r.recording_id}/chunks/${c.idx}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'X-Chunk-SHA256': sha }, body: buf,
    });
    if (!res.ok) throw new Error('chunk ' + res.status);
    c.uploaded = true;
    await put('chunks', c);
    render();
  }
  const fresh = await get('recs', r.client_id);
  if (fresh.state !== 'stopped') return;
  const res = await api(`/api/v1/recordings/${fresh.recording_id}/finalize`, json({ total_chunks: fresh.total_chunks, title: fresh.title }));
  if (res.status === 409) {
    const j = await res.json().catch(() => ({}));
    for (const idx of j.missing || []) {
      const c = await get('chunks', [fresh.client_id, idx]);
      if (c) { c.uploaded = false; await put('chunks', c); again = true; }
    }
    return;
  }
  if (!res.ok) throw new Error('finalize ' + res.status);
  const j = await res.json();
  Object.assign(fresh, { state: 'finalized', meeting_id: j.meeting_id, meeting_url: j.meeting_url, meeting_state: j.meeting_state });
  await put('recs', fresh);
  // The box has the whole recording now: free the phone's storage.
  for (const c of await chunksOf(fresh.client_id)) await del('chunks', [fresh.client_id, c.idx]);
}

// A recording that was running when the app died: keep what was stored and send it.
async function recoverInterrupted() {
  for (const r of await all('recs')) {
    if (r.state !== 'recording' || r.client_id === active) continue;
    const chunks = await chunksOf(r.client_id);
    if (!chunks.length) { await del('recs', r.client_id); continue; }
    r.state = 'stopped';
    r.total_chunks = Math.max(...chunks.map(c => c.idx)) + 1;
    r.interrupted = true;
    await put('recs', r);
  }
}

async function pollMeetings() {
  if (document.visibilityState !== 'visible') return;
  for (const r of await all('recs')) {
    if (r.state !== 'finalized' || ['done', 'failed'].includes(r.meeting_state)) continue;
    try {
      const res = await api(`/api/v1/recordings/${r.recording_id}`);
      if (res.ok) {
        const j = await res.json();
        r.meeting_state = j.meeting_state;
        r.meeting_url = j.meeting_url;
        await put('recs', r);
      }
    } catch (e) { /* offline: try again later */ }
  }
  render();
}

// ---- UI ----

function warn(msg) { $('warn').hidden = false; $('warn').textContent = msg; }

async function render() {
  const recs = (await all('recs')).sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 15);
  $('list').hidden = !recs.length;
  const ul = $('recs');
  ul.textContent = '';
  for (const r of recs) {
    const chunks = r.state === 'finalized' ? [] : await chunksOf(r.client_id);
    const sent = chunks.filter(c => c.uploaded).length;
    const li = document.createElement('li');
    const when = new Date(r.started_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
    const head = document.createElement('div');
    head.textContent = `${r.title || 'Recording'} · ${when} · ${r.storage === 'drive' ? 'Drive' : 'local'}` + (r.interrupted ? ' · interrupted' : '');
    const st = document.createElement('div');
    st.className = 'state';
    if (r.state === 'recording') { st.className += ' pending'; st.textContent = `Recording, ${sent}/${chunks.length} chunks on the box`; }
    else if (r.state === 'stopped') { st.className += ' pending'; st.textContent = `Uploading ${sent}/${r.total_chunks} chunks`; }
    else if (r.meeting_state === 'done') {
      st.className += ' ok';
      const a = document.createElement('a');
      a.href = r.meeting_url; a.textContent = 'Summary ready: open it';
      st.appendChild(a);
    } else if (r.meeting_state === 'failed') { st.className += ' err'; st.textContent = 'Processing failed on the box'; }
    else { st.className += ' pending'; st.textContent = `On the box: ${r.meeting_state || 'queued'}`; }
    li.append(head, st);
    ul.appendChild(li);
  }
}

function storageHint() {
  $('storage-hint').textContent = $('drive').checked
    ? 'On: after processing, the summary, action items and transcript are copied to Google Drive.'
    : 'Off: the recording, transcript and summary stay on the box.';
}

function showPair(msg) {
  $('pair').hidden = false;
  $('recorder').hidden = true;
  $('pair-error').textContent = msg || '';
}

async function pair(t) {
  localStorage.setItem('ir_token', t);
  const r = await fetch('/api/v1/jobs', { headers: { Authorization: 'Bearer ' + t } }).catch(() => null);
  if (!r || !r.ok) { localStorage.removeItem('ir_token'); showPair(r ? 'The box did not accept this token.' : 'Cannot reach the box. Are you on the VPN?'); return false; }
  // Also sign in the web view on this phone, so the summary links open directly.
  await fetch('/login', { method: 'POST', body: new URLSearchParams({ token: t, next: '/' }), redirect: 'manual', credentials: 'same-origin' }).catch(() => {});
  return true;
}

async function boot() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
  if (location.hash.startsWith('#pair=')) {
    const t = decodeURIComponent(location.hash.slice(6));
    history.replaceState(null, '', location.pathname);
    await pair(t);
  }
  $('server').textContent = location.host;
  $('pair-form').addEventListener('submit', async e => {
    e.preventDefault();
    if (await pair($('pair-token').value.trim())) boot();
  });
  $('unpair').addEventListener('click', e => { e.preventDefault(); localStorage.removeItem('ir_token'); showPair(); });
  if (!token()) { showPair(); return; }
  $('pair').hidden = true;
  $('recorder').hidden = false;
  if (!window.isSecureContext || !navigator.mediaDevices) warn('Microphone access needs HTTPS with a certificate this phone trusts (see the README).');
  $('rec').onclick = async () => {
    if (mr && mr.state === 'recording') { stop(); return; }
    try { await start(); } catch (e) { warn('Could not start recording: ' + e.message); stop(); }
  };
  window.addEventListener('online', kick);
  $('drive').addEventListener('change', storageHint);
  // The box's default for new recordings; the switch changes it for this recording only.
  api('/api/v1/settings').then(r => r.ok ? r.json() : null).then(st => {
    if (st && !mr) { $('drive').checked = st.storage_default === 'drive'; storageHint(); }
  }).catch(() => {});
  await recoverInterrupted();
  render();
  kick();
  setInterval(pollMeetings, 15000);
}

boot();
