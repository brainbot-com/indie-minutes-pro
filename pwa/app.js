// Minutes Pro PWA: records in 30 s chunks, keeps every chunk in IndexedDB
// until the box confirms it, uploads over the VPN and resumes after network
// loss or an app restart. The screens follow the Android app (Swiss,
// direction 5): Connect, Ready, Recording and Paused, Settings.
'use strict';

const CHUNK_MS = 30000;
const $ = id => document.getElementById(id);
const LANG = (navigator.language || 'en').toLowerCase().startsWith('de') ? 'de' : 'en';
const STR = {
  en: {
    brand: 'Minutes Pro', back: 'Back', notConnected: 'Not connected', setUp: 'Set up', connectTitle: 'Connect to your recorder',
    pair1: 'In Minutes Pro (in brainbot:agent command, or on your box): Connect a phone.',
    pair2: "Scan the code with this phone's camera and choose Record in the browser.",
    pair3: 'No camera at hand? Type the code here.',
    pairLabel: 'Code', connect: 'Connect', pairUsed: 'This code is used or expired. Make a new one in Minutes Pro.',
    switchAsk: who => `This phone is connected already. Record for ${who || 'someone else'} from now on instead?`,
    pairSlow: 'Too many wrong codes. Wait a few minutes.',
    pairNote: 'This page talks only to the box. Recordings go to this box and nowhere else.',
    pairBad: 'The box did not accept this token.', pairNoBox: 'Cannot reach the box. Is this phone on its network (or the VPN)?',
    pairAgain: "The box no longer accepts this phone's token. Connect it again.",
    newRecording: 'New recording', recording: 'Recording', paused: 'Paused', title: 'Title', optional: 'Optional', untitled: 'Recording',
    box: 'Box', storage: 'Storage', drive: 'Google Drive', local: 'On the box', mic: 'Microphone', micOn: 'On', micOff: 'Off while paused',
    forWhom: 'Recording for', forShort: 'For', phone: 'Phone', record: 'Record', askFirst: 'Tell everyone first', consentAsk: 'Before you record: tell everyone in the meeting that you are recording, and record only if they all agree. In Germany, recording people without their consent is a crime (§ 201 StGB). Asked once on this phone.', privacy: 'Privacy and consent', pause: 'Pause', resume: 'Resume', stop: 'Stop',
    pausedNote: 'Paused. The microphone is off; nothing is recorded until you resume.',
    recordings: 'Recordings', onPhone: 'On this phone', noRecs: 'Nothing recorded yet.',
    meetings: 'Meetings', private: 'Only you see your meetings', noMeetings: 'No meetings yet.',
    gdrive: 'Google Drive', gConnected: e => `Connected: ${e}`, gNot: 'Not connected: meetings set to Drive wait.',
    gExpired: 'Access ended: connect again.', gConnect: 'Connect Google Drive', gDisconnect: 'Disconnect',
    gFinish: 'Finish the sign-in in the new tab.', gDisconnectAsk: 'Disconnect Google Drive? Meetings set to Drive then wait until you connect again.',
    nUploading: n => `${n} uploading`, nWorking: n => `${n} processing`, settings: 'Settings', thisPhone: 'This phone', thisPhoneIs: 'This phone',
    voice: 'Voice processing (echo and noise suppression)', voiceNote: 'Off is better for a phone lying on the table.',
    connection: 'Connection', webView: 'Meetings on the box', webViewCommand: 'Meetings in brainbot:agent command', unpair: 'Disconnect this phone',
    unpairAsk: 'Disconnect this phone from the box? Recordings that are not sent yet stay on the phone.',
    ready: 'ready', offline: 'not reachable', parts: (a, b) => `${a} of ${b} parts`,
    uploading: 'Uploading', working: 'Processing', onBox: 'On the box', summary: 'Summary', failed: 'Failed', interrupted: 'interrupted',
    netOffline: 'Offline', netBox: 'Box not reachable', net: (what, s) => `${what}. The parts stay on the phone. Next try in ${s} s.`,
    noWake: 'This browser cannot keep the screen on. Keep it on yourself while recording.',
    wakeFail: m => `Could not keep the screen on (${m}). Keep it on yourself while recording.`,
    https: 'Microphone access needs HTTPS with a certificate this phone trusts (see the README).',
    startFail: m => 'Could not start recording: ' + m, resumeFail: m => 'Could not switch the microphone back on: ' + m,
  },
  de: {
    brand: 'Minutes Pro', back: 'Zurück', notConnected: 'Nicht verbunden', setUp: 'Einrichten', connectTitle: 'Mit Ihrem Recorder verbinden',
    pair1: 'In Minutes Pro (in brainbot:agent command oder auf Ihrer Box): Telefon verbinden.',
    pair2: 'Scannen Sie den Code mit der Kamera dieses Telefons und wählen Sie Im Browser aufnehmen.',
    pair3: 'Keine Kamera zur Hand? Geben Sie den Code hier ein.',
    pairLabel: 'Code', connect: 'Verbinden', pairUsed: 'Dieser Code ist benutzt oder abgelaufen. Erstellen Sie in Minutes Pro einen neuen.',
    switchAsk: who => `Dieses Telefon ist schon verbunden. Ab jetzt stattdessen für ${who || 'jemand anderen'} aufnehmen?`,
    pairSlow: 'Zu viele falsche Codes. Bitte einige Minuten warten.',
    pairNote: 'Diese Seite spricht nur mit der Box. Aufnahmen gehen an diese Box und nirgendwo sonst hin.',
    pairBad: 'Die Box hat dieses Token nicht angenommen.', pairNoBox: 'Die Box ist nicht erreichbar. Ist das Telefon in ihrem Netz (oder im VPN)?',
    pairAgain: 'Die Box nimmt das Token dieses Telefons nicht mehr an. Bitte neu verbinden.',
    newRecording: 'Neue Aufnahme', recording: 'Aufnahme läuft', paused: 'Pausiert', title: 'Titel', optional: 'Optional', untitled: 'Aufnahme',
    box: 'Box', storage: 'Ablage', drive: 'Google Drive', local: 'Auf der Box', mic: 'Mikrofon', micOn: 'An', micOff: 'Aus, solange pausiert',
    forWhom: 'Aufnahme für', forShort: 'Für', phone: 'Telefon', record: 'Aufnehmen', askFirst: 'Erst allen Bescheid sagen', consentAsk: 'Vor der Aufnahme: Sagen Sie allen im Meeting, dass Sie aufnehmen, und nehmen Sie nur auf, wenn alle einverstanden sind. Menschen ohne ihre Einwilligung aufzunehmen ist in Deutschland strafbar (§ 201 StGB). Diese Frage kommt einmal auf diesem Telefon.', privacy: 'Datenschutz und Einwilligung', pause: 'Pause', resume: 'Weiter', stop: 'Stopp',
    pausedNote: 'Pausiert. Das Mikrofon ist aus; bis Sie fortsetzen, wird nichts aufgenommen.',
    recordings: 'Aufnahmen', onPhone: 'Auf diesem Telefon', noRecs: 'Noch nichts aufgenommen.',
    meetings: 'Meetings', private: 'Ihre Meetings sehen nur Sie', noMeetings: 'Noch keine Meetings.',
    gdrive: 'Google Drive', gConnected: e => `Verbunden: ${e}`, gNot: 'Nicht verbunden: Meetings für Drive warten.',
    gExpired: 'Zugriff abgelaufen: bitte neu verbinden.', gConnect: 'Google Drive verbinden', gDisconnect: 'Trennen',
    gFinish: 'Melden Sie sich im neuen Tab bei Google an.', gDisconnectAsk: 'Google Drive trennen? Meetings für Drive warten dann, bis Sie wieder verbinden.',
    nUploading: n => `${n} ${n === 1 ? 'lädt' : 'laden'} hoch`, nWorking: n => `${n} in Arbeit`, settings: 'Einstellungen', thisPhone: 'Dieses Telefon', thisPhoneIs: 'Dieses Telefon',
    voice: 'Sprachverarbeitung (Echo- und Rauschunterdrückung)', voiceNote: 'Aus ist besser, wenn das Telefon auf dem Tisch liegt.',
    connection: 'Verbindung', webView: 'Meetings auf der Box', webViewCommand: 'Meetings in brainbot:agent command', unpair: 'Dieses Telefon trennen',
    unpairAsk: 'Dieses Telefon von der Box trennen? Noch nicht gesendete Aufnahmen bleiben auf dem Telefon.',
    ready: 'bereit', offline: 'nicht erreichbar', parts: (a, b) => `${a} von ${b} Teilen`,
    uploading: 'Lädt hoch', working: 'In Arbeit', onBox: 'Auf der Box', summary: 'Zusammenfassung', failed: 'Fehler', interrupted: 'unterbrochen',
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

// The app lives at /app/ on the box, or at /indie-recorder/app/ behind a proxy: every
// request goes to the folder above it, so the same files work in both places.
const ROOT = new URL('..', document.baseURI).pathname;
const at = path => ROOT + path.replace(/^\//, '');
const onBox = ROOT === '/';

async function api(path, opts = {}) {
  const r = await fetch(at(path), { ...opts, headers: { Authorization: 'Bearer ' + token(), ...(opts.headers || {}) } });
  if (r.status === 401) throw new AuthError('token not accepted');
  return r;
}
const json = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');

// ---- sealing: each chunk is encrypted before it is stored or sent ----
// A key pair per recording; ECDH with the box's sealing key, HKDF-SHA256, AES-256-GCM
// (recorder/vault.py). Only the box opens the chunks: what waits in IndexedDB or
// passes a proxy is ciphertext. The recording key never leaves this page's memory.

const utf8 = text => new TextEncoder().encode(text);
const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64u = text => Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));

async function sealFor(seal, clientId) {
  const subtle = crypto.subtle;
  const box = await subtle.importKey('raw', unb64u(seal.key), { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const mine = await subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']);
  const shared = await subtle.deriveBits({ name: 'ECDH', public: box }, mine.privateKey, 256);
  const hkdf = await subtle.importKey('raw', shared, 'HKDF', false, ['deriveKey']);
  const key = await subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: utf8('indie-recorder/seal/v1'), info: utf8(clientId) },
    hkdf, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  return { key, kid: seal.kid, epk: b64u(await subtle.exportKey('raw', mine.publicKey)) };
}

async function sealChunk(key, clientId, idx, plain) {
  const iv = new Uint8Array(12);
  new DataView(iv.buffer).setBigUint64(4, BigInt(idx));
  return crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: utf8(`indie-recorder/chunk/v1:${clientId}:${idx}`) },
    key, plain);
}
let sealing = null;                  // { key, kid, epk } of the recording in progress
const storedSeal = () => { try { return JSON.parse(localStorage.getItem('ir_seal')); } catch (e) { return null; } };

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
  const clientId = crypto.randomUUID();
  if (box.seal === undefined) await checkBox();
  const seal = box.seal || storedSeal();                // offline: the key from the last visit (it is public)
  sealing = seal ? await sealFor(seal, clientId) : null;
  const rec = {
    client_id: clientId, mime: mr.mimeType || mime || 'audio/webm',
    title: $('title').value.trim() || null, started_at: new Date().toISOString(),
    storage: on($('drive')) ? 'drive' : 'local',
    seal: sealing ? { kid: sealing.kid, epk: sealing.epk } : null,
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
  const seal = sealing;
  // A promise chain keeps the writes in order, so the stop handler runs after the last chunk is stored.
  chain = chain.then(async () => {
    if (seal) blob = new Blob([await sealChunk(seal.key, cid, idx, await blob.arrayBuffer())]);
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
  const c = $('wave'), w = c.clientWidth, h = c.clientHeight || 88, dpr = window.devicePixelRatio || 1;
  if (!w) return;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
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
  if (token()) { whoAmI(); loadMeetings(); }
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
    const res = await api('/api/v1/recordings', json({ client_id: r.client_id, mime: r.mime, title: r.title, started_at: r.started_at, storage: r.storage,
      ...(r.seal ? { seal: r.seal } : {}) }));
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
  loadMeetings();          // it is one of the person's meetings on the box now
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
  let changed = false;
  for (const r of await all('recs')) {
    if (r.state !== 'finalized' || ['done', 'failed'].includes(r.meeting_state)) continue;
    try {
      const res = await api(`/api/v1/recordings/${r.recording_id}`);
      if (res.ok) {
        const j = await res.json();
        changed = changed || j.meeting_state !== r.meeting_state;
        r.meeting_state = j.meeting_state;
        r.meeting_url = j.meeting_url;
        await put('recs', r);
      }
    } catch (e) { /* offline: try again later */ }
  }
  if (changed) loadMeetings(); else render();
}

// ---- UI ----

let view = 'ready', box = { name: (() => { try { return localStorage.getItem('ir_box') || 'Box'; } catch (e) { return 'Box'; } })(), up: null };

function warn(msg) { $('warn').hidden = false; $('warn').textContent = msg; }

function show(name, error) {
  view = name;
  for (const v of ['pair', 'ready', 'meetings', 'meeting', 'settings']) $(v).hidden = v !== name;
  const sub = name !== 'ready' && name !== 'pair';
  $('back').hidden = !sub;
  $('barLeft').firstElementChild.hidden = sub;
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
    const r = await fetch(at('/api/v1/health'), { cache: 'no-store' });
    const j = await r.json();
    box = { name: j.box || box.name, up: !!(r.ok && j.worker), seal: j.seal || null, drive: j.drive !== false };
    try { localStorage.setItem('ir_box', box.name); } catch (e) { /* private mode */ }
    if (j.seal) try { localStorage.setItem('ir_seal', JSON.stringify(j.seal)); } catch (e) { /* private mode */ }
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
  // on the recorder: one button with how many and what is under way; the list is its own screen
  const busy = recs.filter(r => r.state === 'recording' || r.state === 'stopped').length;
  const working = recs.filter(r => r.state === 'finalized' && !['done', 'failed'].includes(r.meeting_state)).length;
  // the person's meetings on the box (all their phones), at least what this phone recorded
  const n = Math.max(boxMeetings ? boxMeetings.length : 0, recs.length);
  $('recsBtn').hidden = !n && !busy;
  $('recsCount').textContent = String(n);
  $('recsBusy').textContent = busy ? t('nUploading')(busy) : working ? t('nWorking')(working) : '';
  if (view === 'meetings') renderMeetings(recs);
}

/* The person's meetings: what this phone has not handed to the box yet on top,
   then all of their meetings on the box, from any phone, by day; a meeting
   opens with its summary, speakers and transcript. The same views and words as
   Minutes Pro in brainbot:agent command (recorder/static/meetings.js). */
let boxMeetings = null;
const M = IRMeetings.create({ lang: LANG, call: appCall, fail: e => netNote(e), box: () => box.name, drive: () => box.drive !== false });

async function appCall(op, args) {
  const r = await api('/api/v1/app', json({ op, args: args || {} }));
  const d = await r.json().catch(() => ({}));
  if (!r.ok || d.ok === false) throw new Error(d.error || 'HTTP ' + r.status);
  return d.result;
}

function netNote(e) {
  $('net').hidden = false;
  $('net').textContent = e instanceof AuthError ? t('pairAgain') : navigator.onLine ? t('netBox') : t('netOffline');
}

async function loadMeetings() {
  if (!token()) return;
  try {
    boxMeetings = await appCall('list_meetings');
    $('net').hidden = true;
  } catch (e) { netNote(e); }
  render();
}

function renderMeetings(recs) {
  const inflight = recs.filter(r => r.state === 'recording' || r.state === 'stopped').map(r => ({
    title: (r.title || t('untitled')) + (r.interrupted ? ` (${t('interrupted')})` : ''), started_at: r.started_at,
    duration_s: (r.duration_ms || 0) / 1000, state: 'uploading' }));
  M.list($('mlist'), boxMeetings || [], openMeeting, { head: t('onPhone'), rows: inflight });
  $('noRecs').hidden = !!((boxMeetings && boxMeetings.length) || inflight.length);
}

async function openMeeting(id) {
  try { await M.meeting($('meeting'), id); show('meeting'); } catch (e) { netNote(e); }
}

/* Google Drive, as in Minutes Pro: the person connects their own once,
   Google's sign-in in a tab of its own; the recorder then uploads by itself. */
let google = null;
async function loadGoogle() {
  try { renderGoogle((await appCall('status')).google); } catch (e) { $('gBox').hidden = true; }
}
function renderGoogle(g) {
  google = g || null;
  $('gBox').hidden = !(g && g.available);
  if (!g || !g.available) return;
  $('gState').textContent = g.connected ? t('gConnected')(g.email || '') : g.problem === 'expired' ? t('gExpired') : t('gNot');
  $('gBtn').textContent = g.connected ? t('gDisconnect') : t('gConnect');
  $('gBtn').classList.toggle('go', !g.connected);
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
  const r = await fetch(at('/api/v1/pair'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, device: deviceName() }) }).catch(() => null);
  if (!r) { show('pair', t('pairNoBox')); return false; }
  if (r.status === 404) { show('pair', t('pairUsed')); return false; }
  if (r.status === 429) { show('pair', t('pairSlow')); return false; }
  if (!r.ok) { show('pair', t('pairNoBox')); return false; }
  return pair((await r.json()).token);
}

const connect = text => (codeFrom(text) ? redeem(codeFrom(text)) : pair(tokenFrom(text)));

// A link for another connection while this phone has one: ask first, naming whom it would record for
// (someone could send a link to their own account). A code that is used or expired fails as usual.
async function switchOk(link) {
  const code = codeFrom(link);
  const r = await (code ? fetch(at('/api/v1/pair/' + code), { cache: 'no-store' })
    : fetch(at('/api/v1/me'), { headers: { Authorization: 'Bearer ' + tokenFrom(link) } })).catch(() => null);
  if (!r || !r.ok) return true;
  const j = await r.json().catch(() => ({}));
  return confirm(t('switchAsk')(j.person || ''));
}

async function pair(tok) {
  const r = await fetch(at('/api/v1/me'), { headers: { Authorization: 'Bearer ' + tok } }).catch(() => null);
  if (!r || !r.ok) { show('pair', r ? t('pairBad') : t('pairNoBox')); return false; }
  localStorage.setItem('ir_token', tok);
  // Also sign in the web view on this phone, so the summary links open directly.
  if (onBox) await fetch('/login', { method: 'POST', body: new URLSearchParams({ token: tok, next: '/' }), redirect: 'manual', credentials: 'same-origin' }).catch(() => {});
  return true;
}

let wired = false;
function wire() {
  if (wired) return;
  wired = true;
  $('rec').addEventListener('click', async () => {
    // Everyone in the meeting has to know and agree (§ 201 StGB, GDPR): asked once on this phone
    let ok = false;
    try { ok = localStorage.getItem('ir_consent') === '1'; } catch (e) { /* private mode: ask */ }
    if (!ok) {
      if (!confirm(t('consentAsk'))) return;
      try { localStorage.setItem('ir_consent', '1'); } catch (e) { /* private mode */ }
    }
    try { await start(); } catch (e) { warn(t('startFail')(e.message)); stop(); }
  });
  $('privacyLink').href = at('/privacy');
  $('pause').addEventListener('click', () => (paused ? resume() : pause()));
  $('stop').addEventListener('click', stop);
  $('driveBtn').addEventListener('click', () => { setOn($('drive'), !on($('drive'))); storageLabel(); });
  $('voiceproc').addEventListener('click', () => {
    setOn($('voiceproc'), !on($('voiceproc')));
    localStorage.setItem('ir_voice', on($('voiceproc')) ? '1' : '0');
  });
  $('gear').addEventListener('click', () => { show('settings'); loadGoogle(); });
  $('back').addEventListener('click', () => {
    if (view === 'meeting') { M.stop(); show('meetings'); loadMeetings(); } else show('ready');
  });
  $('recsBtn').addEventListener('click', () => { show('meetings'); render(); loadMeetings(); });
  $('gBtn').addEventListener('click', async () => {
    $('gErr').hidden = true;
    try {
      if (google && google.connected) {
        if (!confirm(t('gDisconnectAsk'))) return;
        renderGoogle(await appCall('drive_disconnect'));
        return;
      }
      window.open((await appCall('drive_connect')).url, '_blank', 'noopener');
      $('gState').textContent = t('gFinish');
    } catch (e) { $('gErr').textContent = e.message; $('gErr').hidden = false; }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && view === 'settings') loadGoogle();
  });
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
  // "/" is the box's own pages here, or brainbot:agent command (with the Minutes Pro app) behind its proxy
  $('webView').textContent = onBox ? t('webView') : t('webViewCommand');
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
  $('pairForm').onsubmit = async e => {
    e.preventDefault();
    if (await connect($('pairInput').value)) boot();
  };
  if (location.hash.startsWith('#pair=') || location.hash.startsWith('#code=')) {
    const link = location.hash;
    history.replaceState(null, '', location.pathname);
    if (!token() || await switchOk(link)) await connect(link);
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
  loadMeetings();          // how many meetings the person has on the box, for the button
}

boot();
