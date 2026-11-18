// indie recorder PWA: records in 30 s chunks, keeps every chunk in IndexedDB
// until the box confirms it, uploads over the VPN and resumes after network
// loss or an app restart. The screens follow the Android app (Swiss,
// direction 5): Connect, Ready, Recording and Paused, Settings.
'use strict';

const CHUNK_MS = 30000;
const $ = id => document.getElementById(id);
const LANG = (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en';
const STR = {
  en: {
    brand: 'Indie recorder', back: 'Back', notConnected: 'Not connected', setUp: 'Set up', connectTitle: 'Connect to your recorder',
    pair1: 'In brainbot:agent command, open Meetings, then Settings, then Connect a phone.',
    pair2: "Scan the code with this phone's camera and choose Record in the browser.",
    pair3: 'No camera at hand? Type the code here.',
    pairLabel: 'Code', connect: 'Connect', pairUsed: 'This code is used or expired. Make a new one in Meetings.',
    pairSlow: 'Too many wrong codes. Wait a few minutes.',
    pairNote: 'This page talks only to the box, over the VPN. Recordings go to this box and nowhere else.',
    pairBad: 'The box did not accept this token.', pairNoBox: 'Cannot reach the box. Is the VPN on?',
    pairAgain: "The box no longer accepts this phone's token. Connect it again.",
    newRecording: 'New recording', recording: 'Recording', paused: 'Paused', title: 'Title', optional: 'Optional', untitled: 'Recording',
    box: 'Box', storage: 'Storage', drive: 'Google Drive', local: 'On the box', mic: 'Microphone', micOn: 'On', micOff: 'Off while paused',
    forWhom: 'Recording for', forShort: 'For', phone: 'Phone', record: 'Record', pause: 'Pause', resume: 'Resume', stop: 'Stop',
    pausedNote: 'Paused. The microphone is off; nothing is recorded until you resume.',
    recordings: 'Recordings', settings: 'Settings', thisPhone: 'This phone', thisPhoneIs: 'This phone',
    voice: 'Voice processing (echo and noise suppression)', voiceNote: 'Off is better for a phone lying on the table.',
    connection: 'Connection', webView: 'Meetings on the box', unpair: 'Disconnect this phone',
    unpairAsk: 'Disconnect this phone from the box? Recordings that are not sent yet stay on the phone.',
    ready: 'ready', offline: 'not reachable', parts: (a, b) => `${a} of ${b} parts`,
    uploading: 'Uploading', onBox: 'On the box', summary: 'Summary', failed: 'Failed', interrupted: 'interrupted',
    netOffline: 'Offline', netBox: 'Box not reachable', net: (what, s) => `${what}. The parts stay on the phone. Next try in ${s} s.`,
    noWake: 'This browser cannot keep the screen on. Keep it on yourself while recording.',
    wakeFail: m => `Could not keep the screen on (${m}). Keep it on yourself while recording.`,
    https: 'Microphone access needs HTTPS with a certificate this phone trusts (see the README).',
    startFail: m => 'Could not start recording: ' + m, resumeFail: m => 'Could not switch the microphone back on: ' + m,
  },
  de: {
    brand: 'Indie recorder', back: 'Zurück', notConnected: 'Nicht verbunden', setUp: 'Einrichten', connectTitle: 'Mit Ihrem Recorder verbinden',
    pair1: 'Öffnen Sie in brainbot:agent command Meetings, dann Einstellungen, dann Telefon verbinden.',
    pair2: 'Scannen Sie den Code mit der Kamera dieses Telefons und wählen Sie Im Browser aufnehmen.',
    pair3: 'Keine Kamera zur Hand? Geben Sie den Code hier ein.',
    pairLabel: 'Code', connect: 'Verbinden', pairUsed: 'Dieser Code ist benutzt oder abgelaufen. Erstellen Sie in Meetings einen neuen.',
    pairSlow: 'Zu viele falsche Codes. Bitte einige Minuten warten.',
    pairNote: 'Diese Seite spricht nur mit der Box, über das VPN. Aufnahmen gehen an diese Box und nirgendwo sonst hin.',
    pairBad: 'Die Box hat dieses Token nicht angenommen.', pairNoBox: 'Die Box ist nicht erreichbar. Ist das VPN an?',
    pairAgain: 'Die Box nimmt das Token dieses Telefons nicht mehr an. Bitte neu verbinden.',
    newRecording: 'Neue Aufnahme', recording: 'Aufnahme läuft', paused: 'Pausiert', title: 'Titel', optional: 'Optional', untitled: 'Aufnahme',
    box: 'Box', storage: 'Ablage', drive: 'Google Drive', local: 'Auf der Box', mic: 'Mikrofon', micOn: 'An', micOff: 'Aus, solange pausiert',
    forWhom: 'Aufnahme für', forShort: 'Für', phone: 'Telefon', record: 'Aufnehmen', pause: 'Pause', resume: 'Weiter', stop: 'Stopp',
    pausedNote: 'Pausiert. Das Mikrofon ist aus; bis Sie fortsetzen, wird nichts aufgenommen.',
    recordings: 'Aufnahmen', settings: 'Einstellungen', thisPhone: 'Dieses Telefon', thisPhoneIs: 'Dieses Telefon',
    voice: 'Sprachverarbeitung (Echo- und Rauschunterdrückung)', voiceNote: 'Aus ist besser, wenn das Telefon auf dem Tisch liegt.',
    connection: 'Verbindung', webView: 'Meetings auf der Box', unpair: 'Dieses Telefon trennen',
    unpairAsk: 'Dieses Telefon von der Box trennen? Noch nicht gesendete Aufnahmen bleiben auf dem Telefon.',
    ready: 'bereit', offline: 'nicht erreichbar', parts: (a, b) => `${a} von ${b} Teilen`,
    uploading: 'Lädt hoch', onBox: 'Auf der Box', summary: 'Zusammenfassung', failed: 'Fehler', interrupted: 'unterbrochen',
    netOffline: 'Offline', netBox: 'Box nicht erreichbar', net: (what, s) => `${what}. Die Teile bleiben auf dem Telefon. Nächster Versuch in ${s} s.`,
    noWake: 'Dieser Browser kann den Bildschirm nicht anlassen. Lassen Sie ihn beim Aufnehmen selbst an.',
    wakeFail: m => `Der Bildschirm lässt sich nicht anlassen (${m}). Lassen Sie ihn beim Aufnehmen selbst an.`,
    https: 'Der Mikrofonzugriff braucht HTTPS mit einem Zertifikat, dem dieses Telefon vertraut (siehe README).',
    startFail: m => 'Die Aufnahme ließ sich nicht starten: ' + m, resumeFail: m => 'Das Mikrofon ließ sich nicht wieder einschalten: ' + m,
  },
};
const t = k => (STR[LANG][k] !== undefined ? STR[LANG][k] : STR.en[k]);
const pad = n => String(n).padStart(2, '0');
function length(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000)), h = Math.floor(s / 3600);
  return h ? `${h}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}` : `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
}
const on = el => el.getAttribute('aria-checked') === 'true';
const setOn = (el, v) => el.setAttribute('aria-checked', v ? 'true' : 'false');

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
// The microphone goes through Web Audio into the recorder, so a pause can
// release it (the browser's mic indicator goes off) and a resume can open it
// again while the recorder keeps one continuous file.

let mr = null, ctx = null, dest = null, analyser = null, mic = null, src = null;
let active = null, nextIdx = 0, chain = Promise.resolve();
let recMs = 0, segStart = 0, ticker = null, wakeLock = null, paused = false;
const bars = [];                     // one level per 100 ms of audio, newest last; null is the gap of a pause
const sample = new Float32Array(4096);

async function openMic() {
  const voice = on($('voiceproc'));
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: voice, noiseSuppression: voice, autoGainControl: true },
  });
  src = ctx.createMediaStreamSource(mic);
  src.connect(dest);
  src.connect(analyser);
}

function closeMic() {
  if (src) src.disconnect();
  if (mic) mic.getTracks().forEach(tr => tr.stop());
  src = mic = null;
}

async function start() {
  ctx = new AudioContext();
  dest = ctx.createMediaStreamDestination();
  try { dest.channelCount = 1; } catch (e) { /* stereo then, the box mixes it down */ }
  analyser = ctx.createAnalyser();
  analyser.fftSize = sample.length;
  await openMic();
  const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
    .find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
  mr = new MediaRecorder(dest.stream, mime ? { mimeType: mime, audioBitsPerSecond: 48000 } : {});
  const rec = {
    client_id: crypto.randomUUID(), mime: mr.mimeType || mime || 'audio/webm',
    title: $('title').value.trim() || null, started_at: new Date().toISOString(),
    storage: on($('drive')) ? 'drive' : 'local',
    state: 'recording', next_idx: 0, recording_id: null, total_chunks: null,
  };
  await put('recs', rec);
  active = rec.client_id;
  nextIdx = 0;
  recMs = 0;
  paused = false;
  bars.length = 0;
  const me = mr;
  me.ondataavailable = e => onChunk(rec.client_id, e.data);
  me.onstop = () => onStopped(rec.client_id, me.recordedMs);
  me.start(CHUNK_MS);
  segStart = performance.now();
  ticker = setInterval(tick, 100);
  await lockScreen();
  showRecorder();
  kick();
}

function pause() {
  if (!mr || mr.state !== 'recording') return;
  mr.pause();
  recMs += performance.now() - segStart;
  closeMic();
  paused = true;
  clearInterval(ticker);
  showRecorder();
}

let resuming = false;
async function resume() {
  if (!mr || mr.state !== 'paused' || resuming) return;
  resuming = true;
  try { await openMic(); } catch (e) { warn(t('resumeFail')(e.message)); return; } finally { resuming = false; }
  bars.push(null, null, null);       // the pause shows as a gap in the waveform
  paused = false;
  segStart = performance.now();
  mr.resume();
  ticker = setInterval(tick, 100);
  showRecorder();
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

function onStopped(cid, ms) {
  chain = chain.then(async () => {
    const r = await get('recs', cid);
    r.state = 'stopped';
    r.total_chunks = r.next_idx;
    r.duration_ms = Math.round(ms || 0);
    await put('recs', r);
    active = null;
    kick();
  });
}

function stop() {
  if (mr && mr.state !== 'inactive') {
    if (!paused) recMs += performance.now() - segStart;
    mr.recordedMs = recMs;
    mr.stop();
  }
  closeMic();
  clearInterval(ticker);
  if (wakeLock) wakeLock.release().catch(() => {});
  wakeLock = null;
  if (ctx) ctx.close().catch(() => {});
  ctx = dest = analyser = null;
  mr = null;
  paused = false;
  $('warn').hidden = true;
  $('title').value = '';
  showRecorder();
}

const recordedMs = () => recMs + (mr && !paused ? performance.now() - segStart : 0);
// The waveform's level: the loudest sample of the last 100 ms, -50 dBFS to 0 dBFS onto 0..1.
const level = peak => Math.min(1, Math.max(0, (20 * Math.log10(Math.max(peak, 1e-5)) + 50) / 50));

function tick() {
  if (!analyser) return;
  analyser.getFloatTimeDomainData(sample);
  let peak = 0;
  for (let i = 0; i < sample.length; i++) { const v = Math.abs(sample[i]); if (v > peak) peak = v; }
  bars.push(level(peak));
  if (bars.length > 400) bars.splice(0, bars.length - 400);
  $('clock').textContent = length(recordedMs());
  drawWave();
}

// Bars 3 px wide every 5 px, as tall as their level (at least 2 px), centred,
// the newest at the right edge; grey while paused.
function drawWave() {
  const c = $('wave'), w = c.clientWidth, h = 88, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(paused ? '--dim' : '--ink').trim();
  const shown = bars.slice(-Math.floor((w + 2) / 5));
  shown.forEach((v, i) => {
    if (v === null) return;
    const bh = Math.max(2, Math.round(v * h));
    g.fillRect(w - (shown.length - i) * 5 + 2, Math.round((h - bh) / 2), 3, bh);
  });
}

async function lockScreen() {
  if (!('wakeLock' in navigator)) { warn(t('noWake')); return; }
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => { wakeLock = null; });
  } catch (e) {
    warn(t('wakeFail')(e.message));
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (mr && mr.state === 'recording' && !wakeLock) lockScreen();
  if (token()) whoAmI();
});
window.addEventListener('beforeunload', e => { if (mr) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('resize', drawWave);

// ---- upload: one loop, retried with backoff, resumable at chunk level ----

let running = false, again = false, backoff = 0, retryTimer = null;

async function kick() {
  if (running) { again = true; return; }
  running = true;
  clearTimeout(retryTimer);
  try {
    do { again = false; await uploadAll(); } while (again);
    backoff = 0;
    $('net').hidden = true;
  } catch (e) {
    if (e instanceof AuthError) { show('pair', t('pairAgain')); return; }
    backoff = Math.min(30000, backoff ? backoff * 2 : 2000);  // VPN reconnects may not fire "online"
    $('net').hidden = false;
    $('net').textContent = t('net')(navigator.onLine ? t('netBox') : t('netOffline'), Math.round(backoff / 1000));
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

let view = 'ready', box = { name: 'aibox1', up: null };

function warn(msg) { $('warn').hidden = false; $('warn').textContent = msg; }

function show(name, error) {
  view = name;
  for (const v of ['pair', 'ready', 'settings']) $(v).hidden = v !== name;
  $('back').hidden = name !== 'settings';
  $('barLeft').firstElementChild.hidden = name === 'settings';
  // like the Android app: Settings in the bar on the recorder, the box's state everywhere else
  $('gear').hidden = name !== 'ready';
  $('status').hidden = name === 'ready';
  if (name === 'pair') { $('pairError').textContent = error || ''; $('status').textContent = t('notConnected'); }
  else showBox();
  window.scrollTo(0, 0);
}

function showBox() {
  const state = box.up === null ? '' : box.up ? t('ready') : t('offline');
  $('status').textContent = state ? `${box.name} · ${state}` : box.name;
  $('status').classList.toggle('red', box.up === false);
  if (!mr) $('boxNow').textContent = state ? `${box.name}, ${state}` : box.name;
}

async function checkBox() {
  try {
    const r = await fetch('/api/v1/health', { cache: 'no-store' });
    const j = await r.json();
    box = { name: j.box || box.name, up: !!(r.ok && j.worker) };
  } catch (e) {
    box.up = false;
  }
  if (view !== 'pair') showBox();
}

async function whoAmI() {
  try {
    const r = await api('/api/v1/me');
    if (!r.ok) return;
    const me = await r.json();
    $('device').textContent = me.device || '';
    $('personRow').hidden = !me.person;
    $('person').textContent = me.person || '';
    // whose account this phone records into; without a name, at least which phone it is
    $('forCell').hidden = !me.person && !me.device;
    $('forLabel').textContent = me.person ? t('forShort') : t('phone');
    $('forNow').textContent = me.person || me.device || '';
  } catch (e) { /* offline, or an old box without /me: nothing to show */ }
}

/* Ready, Recording and Paused are one screen in three states, like the phone app. */
function showRecorder() {
  const live = !!mr;
  $('kicker').textContent = !live ? t('newRecording') : paused ? t('paused') : t('recording');
  $('kicker').classList.toggle('live', live && !paused);
  $('clock').classList.toggle('ghost', !live);
  $('clock').classList.toggle('on', live && !paused);
  $('clock').classList.toggle('paused', live && paused);
  if (!live) $('clock').textContent = '00:00';
  $('waveBox').hidden = !live;
  $('titleBox').hidden = live;
  $('titleCell').hidden = !live;
  $('titleNow').textContent = $('title').value.trim() || t('untitled');
  $('micCell').hidden = !live;
  $('micNow').textContent = paused ? t('micOff') : t('micOn');
  $('driveBtn').disabled = live;
  $('rec').hidden = live;
  $('live').hidden = !live;
  $('pauseLabel').textContent = paused ? t('resume') : t('pause');
  $('pauseIcon').innerHTML = paused ? '<polygon points="4,2 24,13 4,24"></polygon>'
    : '<rect x="3" y="2" width="7" height="22"></rect><rect x="16" y="2" width="7" height="22"></rect>';
  $('pause').setAttribute('aria-label', paused ? t('resume') : t('pause'));
  $('pausedNote').hidden = !(live && paused);
  if (live) drawWave();
  showBox();
  render();
}

function storageLabel() { $('driveLabel').textContent = on($('drive')) ? t('drive') : t('local'); }

async function render() {
  const recs = (await all('recs')).filter(r => r.client_id !== active)
    .sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 15);
  if (mr && active) {
    const mine = await chunksOf(active);   // a part every 30 s; until the first one the box line stays as it was
    if (mine.length) $('boxNow').textContent = t('parts')(mine.filter(c => c.uploaded).length, mine.length);
  }
  $('listBox').hidden = !recs.length;
  const list = $('recs');
  list.textContent = '';
  recs.forEach((r, i) => {
    const row = document.createElement(r.meeting_url ? 'a' : 'div');
    row.className = 'row rec';
    if (r.meeting_url) row.href = r.meeting_url;
    let state = t('onBox'), busy = false;
    if (r.state === 'recording' || r.state === 'stopped') { state = t('uploading'); busy = true; }
    else if (r.meeting_state === 'done') state = t('summary');
    else if (r.meeting_state === 'failed') { state = t('failed'); busy = true; }
    const title = (r.title || t('untitled')) + (r.interrupted ? ` (${t('interrupted')})` : '');
    const cells = [['num', pad(i + 1)], ['t', title], ['tnum', r.duration_ms ? length(r.duration_ms) : ''], ['st' + (busy ? ' busy' : ''), state]];
    for (const [cls, text] of cells) {
      const s = document.createElement('span');
      s.className = cls;
      s.textContent = text;
      row.appendChild(s);
    }
    list.appendChild(row);
  });
}

// A link from an older pairing (".../app/#pair=TOKEN") or a bare token.
function tokenFrom(text) {
  const m = /#pair=([^&\s]+)/.exec(text || '');
  return m ? decodeURIComponent(m[1]) : (text || '').trim();
}

// A connect code, typed ("K7Q4-M2XB") or in a link ("...#code=K7Q4M2XB", "...connect#K7Q4M2XB").
function codeFrom(text) {
  const tail = String(text || '').split('#').pop().replace(/^code=/, '');
  const code = tail.toUpperCase().replace(/[^23456789ABCDEFGHJKMNPQRSTUVWXYZ]/g, '');
  return code.length === 8 && !/irt_/i.test(text) ? code : null;
}

const deviceName = () => {
  const ua = navigator.userAgent;
  const where = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) ? 'iPad' : /Android/.test(ua) ? 'Android' : 'computer';
  return LANG === 'de' ? `Browser (${where})` : `Browser on ${where}`;
};

// Spends a connect code: the box answers with this phone's own token.
async function redeem(code) {
  const r = await fetch('/api/v1/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, device: deviceName() }) }).catch(() => null);
  if (!r) { show('pair', t('pairNoBox')); return false; }
  if (r.status === 404) { show('pair', t('pairUsed')); return false; }
  if (r.status === 429) { show('pair', t('pairSlow')); return false; }
  if (!r.ok) { show('pair', t('pairNoBox')); return false; }
  return pair((await r.json()).token);
}

const connect = text => (codeFrom(text) ? redeem(codeFrom(text)) : pair(tokenFrom(text)));

async function pair(tok) {
  const r = await fetch('/api/v1/me', { headers: { Authorization: 'Bearer ' + tok } }).catch(() => null);
  if (!r || !r.ok) { show('pair', r ? t('pairBad') : t('pairNoBox')); return false; }
  localStorage.setItem('ir_token', tok);
  // Also sign in the web view on this phone, so the summary links open directly.
  await fetch('/login', { method: 'POST', body: new URLSearchParams({ token: tok, next: '/' }), redirect: 'manual', credentials: 'same-origin' }).catch(() => {});
  return true;
}

let wired = false;
function wire() {
  if (wired) return;
  wired = true;
  $('rec').addEventListener('click', async () => {
    try { await start(); } catch (e) { warn(t('startFail')(e.message)); stop(); }
  });
  $('pause').addEventListener('click', () => (paused ? resume() : pause()));
  $('stop').addEventListener('click', stop);
  $('driveBtn').addEventListener('click', () => { setOn($('drive'), !on($('drive'))); storageLabel(); });
  $('voiceproc').addEventListener('click', () => {
    setOn($('voiceproc'), !on($('voiceproc')));
    localStorage.setItem('ir_voice', on($('voiceproc')) ? '1' : '0');
  });
  $('gear').addEventListener('click', () => show('settings'));
  $('back').addEventListener('click', () => show('ready'));
  $('unpair').addEventListener('click', () => {
    if (!confirm(t('unpairAsk'))) return;
    if (mr) stop();
    localStorage.removeItem('ir_token');
    show('pair');
  });
  window.addEventListener('online', () => { kick(); checkBox(); });
  setInterval(pollMeetings, 15000);
  setInterval(checkBox, 30000);
}

async function boot() {
  document.documentElement.lang = LANG;
  document.querySelectorAll('[data-t]').forEach(el => { el.textContent = t(el.dataset.t); });
  document.querySelectorAll('[data-tp]').forEach(el => { el.placeholder = t(el.dataset.tp); });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
  $('pairForm').onsubmit = async e => {
    e.preventDefault();
    if (await connect($('pairInput').value)) boot();
  };
  if (location.hash.startsWith('#pair=') || location.hash.startsWith('#code=')) {
    const link = location.hash;
    history.replaceState(null, '', location.pathname);
    await connect(link);
  }
  $('server').textContent = location.host;
  checkBox();
  if (!token()) { show('pair'); return; }
  setOn($('voiceproc'), localStorage.getItem('ir_voice') === '1');
  wire();
  show('ready');
  showRecorder();
  storageLabel();
  if (!window.isSecureContext || !navigator.mediaDevices) warn(t('https'));
  // The box's default for new recordings; the switch changes it for this recording only.
  api('/api/v1/settings').then(r => (r.ok ? r.json() : null)).then(st => {
    if (st && !mr) { setOn($('drive'), st.storage_default === 'drive'); storageLabel(); }
  }).catch(() => {});
  whoAmI();
  await recoverInterrupted();
  render();
  kick();
}

boot();
