/* The meeting views every Minutes Pro app shares: the app in brainbot:agent
   command (integrations/indie-chat/inline_assets.py puts this file into it)
   and the phone web app; the Android app draws the same screens with the same
   words. A person's meetings by day, and one meeting: its summary, its
   speakers and their names, its transcript. Plain DOM. The host says how to
   reach the box, call(op, args) with the ops of the "indie-recorder"
   connector, in which language, what the box is called (box), whether
   meetings can go to Google Drive there (drive) and whether to link to the
   box's own meeting page (boxLink). In a team (an Indie Box): readOnly for a
   member, who reads what the owner shares; team, the owner's switch that
   shares a meeting; canDelete and onDeleted, deleting a meeting for good. */
(function () {
  'use strict';
  const STR = {
    en: {
      today: 'Today', yesterday: 'Yesterday', local: 'Local', processing: 'Processing', failed: 'Failed', uploading: 'Uploading',
      notYet: 'Not yet', length: 'Length', speakers: 'Speakers', drive: 'Google Drive', openFolder: 'Open folder',
      noteLocal: 'Stays on {box}.', noteWaiting: 'Waiting for the Drive upload.', noteDone: 'Stored in Drive.',
      queued: 'A new summary is on its way.', saved: 'Saved.',
      speakerNames: 'Speaker names', saveNames: 'Save names', unnamed: (n) => ` · ${n} without a name`,
      talk: (share, turns, first) => `${share}% of the talk · ${turns} turns · first at ${first}`,
      play: 'Play', stopPlay: 'Stop', use: (n) => `Use "${n}"`, nameLabel: 'Name',
      by: { recorder: 'In the meeting', agent: 'Your agent', person: 'You' },
      accentGuess: (s) => `Accent, a guess: ${s}`,
      accents: { english: 'English (England)', american: 'American', scottish: 'Scottish', irish: 'Irish', canadian: 'Canadian',
        northernirish: 'Northern Irish', indian: 'Indian', spanish: 'Spanish', dutch: 'Dutch', german: 'German', czech: 'Czech',
        polish: 'Polish', french: 'French', italian: 'Italian', hungarian: 'Hungarian', finnish: 'Finnish', vietnamese: 'Vietnamese',
        romanian: 'Romanian', slovak: 'Slovak', estonian: 'Estonian', lithuanian: 'Lithuanian', croatian: 'Croatian', slovene: 'Slovene' },
      transcript: 'Transcript', more: 'More', newSummary: 'Write a new summary', openBox: 'Open on {box}',
      tldr: 'Summary', decisions: 'Decisions', actions: 'Action items', questions: 'Open questions',
      team: 'Team', deleteMeeting: 'Delete meeting', noName: 'No name yet',
      deleteAsk: 'Delete this meeting for good? Its recording, transcript and summaries go from {box}. Copies in Google Drive stay there.',
    },
    de: {
      today: 'Heute', yesterday: 'Gestern', local: 'Lokal', processing: 'In Arbeit', failed: 'Fehler', uploading: 'Lädt hoch',
      notYet: 'Noch nicht', length: 'Länge', speakers: 'Sprecher', drive: 'Google Drive', openFolder: 'Ordner öffnen',
      noteLocal: 'Bleibt auf {box}.', noteWaiting: 'Wartet auf das Hochladen nach Drive.', noteDone: 'In Drive abgelegt.',
      queued: 'Eine neue Zusammenfassung ist unterwegs.', saved: 'Gespeichert.',
      speakerNames: 'Namen der Sprecher', saveNames: 'Namen speichern', unnamed: (n) => ` · ${n} ohne Namen`,
      talk: (share, turns, first) => `${share} % der Redezeit · ${turns} Beiträge · zuerst bei ${first}`,
      play: 'Anhören', stopPlay: 'Stopp', use: (n) => `"${n}" übernehmen`, nameLabel: 'Name',
      by: { recorder: 'Im Meeting', agent: 'Ihr Agent', person: 'Sie' },
      accentGuess: (s) => `Akzent, geschätzt: ${s}`,
      accents: { english: 'Englisch (England)', american: 'Amerikanisch', scottish: 'Schottisch', irish: 'Irisch', canadian: 'Kanadisch',
        northernirish: 'Nordirisch', indian: 'Indisch', spanish: 'Spanisch', dutch: 'Niederländisch', german: 'Deutsch', czech: 'Tschechisch',
        polish: 'Polnisch', french: 'Französisch', italian: 'Italienisch', hungarian: 'Ungarisch', finnish: 'Finnisch', vietnamese: 'Vietnamesisch',
        romanian: 'Rumänisch', slovak: 'Slowakisch', estonian: 'Estnisch', lithuanian: 'Litauisch', croatian: 'Kroatisch', slovene: 'Slowenisch' },
      transcript: 'Transkript', more: 'Mehr', newSummary: 'Neue Zusammenfassung schreiben', openBox: 'Auf {box} öffnen',
      tldr: 'Zusammenfassung', decisions: 'Entscheidungen', actions: 'Aufgaben', questions: 'Offene Fragen',
      team: 'Team', deleteMeeting: 'Meeting löschen', noName: 'Noch ohne Namen',
      deleteAsk: 'Dieses Meeting endgültig löschen? Aufnahme, Transkript und Zusammenfassungen werden von {box} entfernt. Kopien in Google Drive bleiben dort.',
    },
  };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // links that agents report (a Drive folder) only as web addresses: javascript: would run
  const webUrl = (u) => (/^https?:\/\/[^\s]+$/i.test(String(u || '').trim()) ? String(u).trim() : '');
  const pad = (n) => String(n).padStart(2, '0');

  function create({ lang, call, fail = () => {}, onChange = () => {}, box = () => 'aibox1', drive = () => true, boxLink = true,
                    readOnly = () => false, team = () => false, canDelete = () => false, onDeleted = () => {} }) {
    const L = lang === 'de' ? 'de' : 'en';
    const t = (k) => {
      const v = STR[L][k] !== undefined ? STR[L][k] : STR.en[k];
      return typeof v === 'string' ? v.replace(/\{box\}/g, box()) : v;
    };
    const dayKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;   // the local day
    function dayLabel(d) {
      if (dayKey(d) === dayKey(new Date())) return t('today');
      if (dayKey(d) === dayKey(new Date(Date.now() - 864e5))) return t('yesterday');
      return d.toLocaleDateString(L, { weekday: 'long', day: 'numeric', month: 'long' });
    }
    const time = (d) => d.toLocaleTimeString(L, { hour: '2-digit', minute: '2-digit' });
    function length(s) {
      s = Math.max(0, Math.round(s || 0));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
      return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
    }
    function stateCell(m) {
      if (m.state === 'uploading') return `<span class="state busy">${esc(t('uploading'))}</span>`;
      if (m.state === 'failed') return `<span class="state busy">${esc(t('failed'))}</span>`;
      if (m.state !== 'done') return `<span class="state busy">${esc(t('processing'))}</span>`;
      return `<span class="state">${esc(m.storage === 'drive' ? 'Drive' : t('local'))}</span>`;
    }
    const row = (m, n, d, tag) => `<${tag} ${tag === 'button' ? 'type="button" ' : ''}class="row mrow"${m.id ? ` data-id="${esc(m.id)}"` : ''}>` +
      `<span class="num">${pad(n)}</span><span class="title">${esc(m.title || m.id)}</span><span class="when tnum">${esc(time(d))}</span>` +
      `<span class="len tnum">${esc(length(m.duration_s))}</span>${stateCell(m)}</${tag}>`;

    /* The list by day, newest first. extra: what is not on the box yet (a
       phone's own uploads), {head, rows: [{title, started_at, duration_s, state}]}. */
    function list(el, meetings, onOpen, extra) {
      el.classList.add('irm', 'list');
      let html = extra && extra.rows.length
        ? `<div><h2 class="section">${esc(extra.head)}</h2><div class="rows">` +
          extra.rows.map((m, i) => row(m, i + 1, new Date(m.started_at), 'div')).join('') + '</div></div>'
        : '';
      let open = null, n = 0;
      for (const m of meetings) {
        const d = new Date(m.started_at || m.updated_at);
        if (dayKey(d) !== open) {
          if (open) html += '</div></div>';
          html += `<div><h2 class="section">${esc(dayLabel(d))}</h2><div class="rows">`;
          open = dayKey(d); n = 0;
        }
        html += row(m, ++n, d, 'button');
      }
      el.innerHTML = html + (open ? '</div></div>' : '');
      el.querySelectorAll('button.mrow').forEach((b) => b.addEventListener('click', () => onOpen(b.dataset.id)));
    }

    /* The recorder's summary as Swiss sections: the short version as the lead,
       then a section per heading with numbered rows; tables of action items
       become rows with the owner and the date on the right. */
    function summary(markdown) {
      const lines = String(markdown || '').split('\n').filter((l) => !/^# |^_.*_$|^https?:\/\/\S+$/.test(l.trim()));
      const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      const names = { 'tl;dr': 'tldr', kurzfassung: 'tldr', decisions: 'decisions', entscheidungen: 'decisions',
        'action items': 'actions', aufgaben: 'actions', 'open questions': 'questions', 'offene fragen': 'questions' };
      const parts = [];
      let cur = '', rows = null, n = 0, inTldr = false, first = true;
      const flush = () => { if (rows !== null) { cur += `<div class="rows">${rows}</div>`; rows = null; } };
      const next = (head) => { flush(); if (cur) parts.push(`<div>${cur}</div>`); cur = head || ''; n = 0; };
      const add = (html, side) => {
        n += 1;
        rows = (rows || '') + `<div class="row"><span class="num">${pad(n)}</span><span>${html}</span><span class="side">${esc(side || '')}</span></div>`;
      };
      for (const raw of lines) {
        const line = raw.trim();
        let m;
        if ((m = /^#{2,4}\s+(.*)/.exec(line))) {
          const key = names[m[1].toLowerCase()];
          inTldr = key === 'tldr';
          next(inTldr ? '' : `<h2 class="section">${esc(key ? t(key) : m[1])}</h2>`);
          continue;
        }
        if (/^\|[\s:|-]+\|$/.test(line) || /^\|\s*#\s*\|/.test(line)) continue;
        if ((m = /^\|(.*)\|$/.exec(line))) {
          const c = m[1].split('|').map((x) => x.trim());
          add(inline(c[1] || c[0]), [c[2], c[3]].filter((x) => x && !/^[-\u2013\u2014]$/.test(x)).join(' · '));
          continue;
        }
        if ((m = /^[-*]\s+(.*)/.exec(line))) { add(inline(m[1])); continue; }
        flush();
        if (line) { cur += (inTldr || first) ? `<p class="lead">${inline(line)}</p>` : `<p class="note">${inline(line)}</p>`; first = false; }
      }
      next();
      return parts.join('');
    }

    /* The transcript (render.py's Markdown) as turns: who and when, then what
       they said; the title line is the meeting's own, so it goes. */
    function transcript(markdown) {
      let html = '', turn = false, meta = true;
      for (const raw of String(markdown || '').split('\n')) {
        const line = raw.trim();
        let m;
        if (!line || /^# /.test(line)) continue;
        if ((m = /^\*\*(.+?)\*\*\s*·\s*([\d:]+)$/.exec(line))) {
          html += (turn ? '</div>' : '') + `<div class="turn"><p class="who"><strong>${esc(m[1])}</strong> <span class="muted tnum">${esc(m[2])}</span></p>`;
          turn = true; meta = false;
        } else if (meta || /^> /.test(line)) {
          html += `<p class="muted trmeta">${esc(line.replace(/^> /, ''))}</p>`;
          meta = false;
        } else {
          html += `<p>${esc(line)}</p>`;
        }
      }
      return html + (turn ? '</div>' : '');
    }

    /* Speaker cards, the most talkative first. Play: seven seconds of the voice
       from this meeting (a signed link, two hours). The accent: a guess from up
       to 30 s of their voice (engines/accent), only for English meetings, the
       likelier ones only. Use: puts a suggested name in the field; Save names
       applies them everywhere. */
    const accent = (list) => (list || []).filter((a) => a.p >= 0.1)
      .map((a) => `${t('accents')[a.accent] || a.accent} ${Math.round(a.p * 100)}%`).join(' · ');
    function speakers(cards, ro) {
      return cards.map((c, i) => `<div class="spk">
        <div class="spkhead"><span class="num">${pad(i + 1)}</span><span class="who">${esc(c.label)}</span>
          <span class="side">${c.turns ? esc(t('talk')(c.share, c.turns, length(c.first))) : ''}</span>
          ${c.clip_url ? `<button type="button" class="textbtn play" data-url="${esc(c.clip_url)}">${esc(t('play'))}</button>` : '<span></span>'}</div>
        ${accent(c.accent) ? `<p class="said accent">${esc(t('accentGuess')(accent(c.accent)))}</p>` : ''}
        ${(c.lines || []).map((l) => `<p class="said">"${esc(l)}"</p>`).join('')}
        ${ro ? '' : (c.suggestions || []).map((x) => `<p class="sugg"><button type="button" class="textbtn use" data-label="${esc(c.label)}" data-name="${esc(x.name)}">${esc(t('use')(x.name))}</button>` +
          `<span class="muted">${esc(t('by')[x.by] || '')}${x.why ? ': ' + esc(x.why) : ''}</span></p>`).join('')}
        ${ro ? `<div class="cell"><span class="label">${esc(t('nameLabel'))}</span><span class="v">${esc(c.name || t('noName'))}</span></div>`
          : `<label class="cell"><span class="label">${esc(t('nameLabel'))}</span><input class="line" data-label="${esc(c.label)}" value="${esc(c.name)}"></label>`}
      </div>`).join('');
    }

    const player = new Audio();
    let playing = null, current = null;
    player.addEventListener('ended', () => { if (playing) playing.textContent = t('play'); playing = null; });
    function stop() { player.pause(); if (playing) playing.textContent = t('play'); playing = null; }

    /* One meeting into el. keepOpen: drawn again after a change, with the
       transcript and the speakers left open as they were. */
    async function meeting(el, id, keepOpen) {
      const m = await call('get_meeting', { meeting_id: id, include_transcript: false });
      const was = keepOpen && current && current.id === m.id && el.querySelector('#dTrBox')
        ? { tr: el.querySelector('#dTr').innerHTML, trOpen: el.querySelector('#dTrBox').open, spkOpen: el.querySelector('#dSpkBox') && el.querySelector('#dSpkBox').open }
        : null;
      stop();
      current = m;
      el.classList.add('irm');
      const d = new Date(m.started_at || m.created_at);
      // speakers: the cards from get_meeting (an older recorder sent only how many)
      const cards = Array.isArray(m.speakers) ? m.speakers : Object.entries(m.speaker_names || {}).map(([label, name]) =>
        ({ label, name, suggestions: [], lines: [], share: 0, turns: 0, first: 0 }));
      const nSpk = Array.isArray(m.speakers) ? m.speakers.length : m.speakers;
      const unnamed = cards.filter((c) => !c.name).length;
      const ro = readOnly();
      const withDrive = !ro && (drive() || m.storage === 'drive');
      const withTeam = !ro && team();
      const note = m.storage !== 'drive' ? t('noteLocal') : (m.drive_url ? t('noteDone') : t('noteWaiting'));
      el.innerHTML = `<div class="head"><span class="kicker" id="dWhen">${esc(d.toLocaleDateString(L, { day: 'numeric', month: 'short', year: 'numeric' }))} · ${esc(time(d))}</span>` +
        `<h1 class="h2" id="dTitle">${esc(m.title || m.id)}</h1></div>
        <div>
          <div class="cells">
            <div class="cell"><span class="label">${esc(t('length'))}</span><span class="v tnum" id="dLen">${esc(length(m.duration_s))}</span></div>
            <div class="cell"><span class="label">${esc(t('speakers'))}</span><span class="v" id="dSpkN">${esc(nSpk ? String(nSpk) : t('notYet'))}</span></div>
            ${withDrive ? `<div class="cell"><span class="label" id="dLDrive">${esc(t('drive'))}</span><button type="button" class="switch" role="switch" aria-checked="${m.storage === 'drive'}" id="dDrive" aria-labelledby="dLDrive"></button></div>` : ''}
            ${withTeam ? `<div class="cell"><span class="label" id="dLTeam">${esc(t('team'))}</span><button type="button" class="switch" role="switch" aria-checked="${!!m.shared}" id="dTeam" aria-labelledby="dLTeam"></button></div>` : ''}
          </div>
          <p class="note" style="margin-top: 12px"><span id="dNote">${esc(note)}</span> ${webUrl(m.drive_url) ? `<a id="dLink" target="_blank" rel="noopener" href="${esc(webUrl(m.drive_url))}">${esc(t('openFolder'))}</a>` : ''}</p>
        </div>
        <div class="sum" id="dSum">${summary(m.summary_md)}</div>
        <div class="folds">
          <details class="fold" id="dSpkBox"${cards.length ? '' : ' hidden'}>
            <summary>${esc(t('speakerNames') + (unnamed ? t('unnamed')(unnamed) : ''))}</summary>
            <div class="inner"><div class="names" id="dSpk">${speakers(cards, ro)}</div>${ro ? '' : `<button type="button" class="btn" id="dSave">${esc(t('saveNames'))}</button>`}</div>
          </details>
          <details class="fold" id="dTrBox"><summary>${esc(t('transcript'))}</summary><div class="inner"><div class="tr" id="dTr"></div></div></details>
          ${ro ? '' : `<details class="fold"><summary>${esc(t('more'))}</summary>
            <div class="inner more"><button type="button" class="btn" id="dRedo">${esc(t('newSummary'))}</button>${m.url && boxLink ? `<a id="dBox" target="_blank" rel="noopener" href="${esc(m.url)}">${esc(t('openBox'))}</a>` : ''}${canDelete() ? `<button type="button" class="textbtn danger" id="dDelete">${esc(t('deleteMeeting'))}</button>` : ''}</div>
          </details>`}
        </div>`;
      const $ = (s) => el.querySelector(s);
      if ($('#dDrive')) $('#dDrive').addEventListener('click', async () => {
        const drive = $('#dDrive').getAttribute('aria-checked') !== 'true';
        $('#dDrive').setAttribute('aria-checked', String(drive));
        try {
          await call('set_storage', { meeting_id: m.id, storage: drive ? 'drive' : 'local' });
          onChange(m.id);
          await meeting(el, m.id, true);
        } catch (e) { $('#dDrive').setAttribute('aria-checked', String(!drive)); fail(e); }
      });
      if ($('#dTeam')) $('#dTeam').addEventListener('click', async () => {
        const shared = $('#dTeam').getAttribute('aria-checked') !== 'true';
        $('#dTeam').setAttribute('aria-checked', String(shared));
        try { await call('share_meeting', { meeting_id: m.id, shared }); m.shared = shared ? 1 : 0; onChange(m.id); }
        catch (e) { $('#dTeam').setAttribute('aria-checked', String(!shared)); fail(e); }
      });
      if ($('#dDelete')) $('#dDelete').addEventListener('click', async () => {
        if (!confirm(t('deleteAsk'))) return;
        try { stop(); await call('delete_meeting', { meeting_id: m.id }); current = null; onDeleted(m.id); } catch (e) { fail(e); }
      });
      $('#dSpk').querySelectorAll('.play').forEach((b) => b.addEventListener('click', () => {
        const again = playing === b;
        stop();
        if (again) return;
        player.src = b.dataset.url;
        player.play().then(() => { playing = b; b.textContent = t('stopPlay'); }).catch(fail);
      }));
      $('#dSpk').querySelectorAll('.use').forEach((b) => b.addEventListener('click', () => {
        const input = [...$('#dSpk').querySelectorAll('input')].find((x) => x.dataset.label === b.dataset.label);
        if (input) { input.value = b.dataset.name; input.focus(); }
      }));
      if ($('#dSave')) $('#dSave').addEventListener('click', async () => {
        const names = {};
        $('#dSpk').querySelectorAll('input').forEach((i) => { names[i.dataset.label] = i.value; });
        try {
          await call('rename_speakers', { meeting_id: m.id, names });
          onChange(m.id);
          await meeting(el, m.id, true);
          el.querySelector('#dNote').textContent = t('saved');
        } catch (e) { fail(e); }
      });
      const loadTranscript = async () => {
        if (!$('#dTrBox').open || $('#dTr').textContent) return;
        try { $('#dTr').innerHTML = transcript((await call('get_meeting', { meeting_id: m.id, include_transcript: true })).transcript_md); }
        catch (e) { fail(e); }
      };
      $('#dTrBox').addEventListener('toggle', loadTranscript);
      if ($('#dRedo')) $('#dRedo').addEventListener('click', async () => {
        try { await call('new_summary', { meeting_id: m.id }); $('#dNote').textContent = t('queued'); } catch (e) { fail(e); }
      });
      if (was) {
        $('#dTr').innerHTML = was.tr;
        $('#dTrBox').open = was.trOpen;
        if ($('#dSpkBox')) $('#dSpkBox').open = !!was.spkOpen;
      }
      return m;
    }

    return { t, dayLabel, time, length, list, summary, transcript, meeting, stop, current: () => current };
  }

  window.IRMeetings = { create, STR };
})();
