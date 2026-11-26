// Live extras for the demo: posture pop-ups, alarm sound and the labelled test-recording panel.
// Loaded after app.js and uses its globals: t, lang, events, tracks, isAlert, kindOf, fmtTime, api, node, $.
(function () {
  'use strict';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) {} },
  };

  // ------------------------------------------------------------ sound
  // Browsers only allow sound after the first click or key press on the page.
  const SOUND_KEY = 'radarsense-sound';
  let soundOn = store.get(SOUND_KEY) !== 'off', actx = null;
  function audio() {
    if (!actx) { const C = window.AudioContext || window.webkitAudioContext; if (!C) return null; actx = new C(); }
    return actx;
  }
  const soundReady = () => !!actx && actx.state === 'running';
  function unlock() { const a = audio(); if (a && a.state === 'suspended') a.resume().then(paintSound, paintSound); paintSound(); }
  document.addEventListener('pointerdown', unlock, true);
  document.addEventListener('keydown', unlock, true);
  function tone(freq, start, dur, vol, type) {
    if (!soundOn || !soundReady()) return;
    const a = actx, o = a.createOscillator(), g = a.createGain(), t0 = a.currentTime + start;
    o.type = type || 'square'; o.frequency.value = freq;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(vol, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(a.destination); o.start(t0); o.stop(t0 + dur + 0.05);
  }
  function siren() { for (let i = 0; i < 3; i++) { tone(880, i * 0.5, 0.22, 0.22); tone(660, i * 0.5 + 0.25, 0.22, 0.22); } }
  function chime() { tone(988, 0, 0.12, 0.05, 'sine'); tone(1319, 0.1, 0.18, 0.045, 'sine'); }

  const soundBtn = node('button', '', 'btn sound-btn');
  soundBtn.type = 'button';
  soundBtn.addEventListener('click', () => { soundOn = !soundOn; store.set(SOUND_KEY, soundOn ? 'on' : 'off'); paintSound(); if (soundOn) chime(); });
  const right = document.querySelector('.appbar-right');
  if (right) right.insertBefore(soundBtn, $('#themeToggle'));
  const unlockNote = node('div', '', 'sound-unlock');
  document.body.append(unlockNote);
  function paintSound() {
    soundBtn.textContent = t(soundOn ? 'sound.on' : 'sound.off');
    soundBtn.classList.toggle('is-off', !soundOn);
    soundBtn.setAttribute('aria-pressed', String(soundOn));
    unlockNote.textContent = t('sound.unlock');
    unlockNote.hidden = !soundOn || soundReady();
  }

  // ------------------------------------------------------------ pop-ups
  const stack = node('div', '', 'toast-stack');
  stack.setAttribute('aria-live', 'assertive');
  document.body.append(stack);
  const MAX_POSTURE_TOASTS = 4;

  function postureToast(id, posture) {
    const el = node('div', '', 'toast toast-' + posture);
    el.append(node('span', '', 'toast-dot'), node('span', t('toast.posture', { id, p: t('posture.' + posture) }), 'toast-text'));
    stack.append(el);
    const old = stack.querySelectorAll('.toast:not(.toast-alert)');
    if (old.length > MAX_POSTURE_TOASTS) old[0].remove();
    setTimeout(() => { el.classList.add('is-leaving'); setTimeout(() => el.remove(), 400); }, 3500);
  }

  const alertToasts = new Map();   // event id -> element
  const SIREN_S = 20;
  let newestAlertAt = 0;
  function alertToast(e) {
    const el = node('div', '', 'toast toast-alert');
    const body = node('div', '', 'toast-body');
    body.append(node('strong', t(kindOf(e) === 'fall' ? 'toast.fall' : 'toast.prolonged'), 'toast-title'),
                node('span', t('toast.alertSub', { id: e.track_id, time: fmtTime(e.timestamp) }), 'toast-sub'));
    const b = node('button', t('toast.ack'), 'btn');
    b.type = 'button';
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api('/api/events/' + e.id + '/ack', { method: 'POST', headers: { 'X-Radar-Action': 'ack' } });
        e.acknowledged = true; el.remove(); alertToasts.delete(e.id);
        if (typeof paintEvents === 'function') paintEvents(true);
      } catch (_) { b.disabled = false; }
    });
    el.append(body, b);
    stack.prepend(el);
    alertToasts.set(e.id, el);
  }

  // Posture changes come from /api/state (polled by app.js); one pop-up per change per person.
  const lastPosture = new Map();
  function watchPostures() {
    const seen = new Set();
    for (const tr of tracks()) {
      if (!tr.posture) continue;
      seen.add(tr.track_id);
      if (lastPosture.get(tr.track_id) !== tr.posture) {
        lastPosture.set(tr.track_id, tr.posture);
        if (tr.posture !== 'fallen') { postureToast(tr.track_id, tr.posture); chime(); }
      }
    }
    for (const id of [...lastPosture.keys()]) if (!seen.has(id)) lastPosture.delete(id);
  }

  // Unacknowledged alerts keep a red pop-up on screen and the alarm repeating until acknowledged.
  function watchAlerts() {
    const pending = (Array.isArray(events) ? events : []).filter(e => isAlert(e) && !e.acknowledged);
    const ids = new Set(pending.map(e => e.id));
    for (const [id, el] of alertToasts) if (!ids.has(id)) { el.remove(); alertToasts.delete(id); }
    for (const e of pending.slice(0, 3)) if (!alertToasts.has(e.id)) { alertToast(e); siren(); newestAlertAt = Date.now(); }
    return pending.length;
  }
  setInterval(watchPostures, 250);
  let lastSiren = 0;
  setInterval(() => {
    const n = watchAlerts();
    // The siren repeats for SIREN_S after the newest alert; the red pop-up stays until acknowledged.
    if (n && Date.now() - lastSiren > 2500 && Date.now() - newestAlertAt < SIREN_S * 1000) { siren(); lastSiren = Date.now(); }
    paintSound();
  }, 500);

  // ------------------------------------------------------------ test recording panel
  const LABEL_KEY = 'radarsense-rec-label';
  const panel = node('section', '', 'rec-panel');
  const head = node('div', '', 'rec-head');
  const title = node('h2', '', 'label');
  const dot = node('span', '', 'rec-dot');
  head.append(dot, title);
  const row = node('div', '', 'rec-row');
  const select = document.createElement('select');
  select.className = 'rec-select';
  const btn = node('button', '', 'btn btn-primary rec-btn');
  btn.type = 'button';
  row.append(select, btn);
  const status = node('p', '', 'rec-status');
  panel.append(head, row, status);
  const people = document.querySelector('aside.people');
  if (people) people.prepend(panel);

  let rec = { active: false, labels: [] }, recError = '';
  function paintRec() {
    title.textContent = t('rec.title');
    select.setAttribute('aria-label', t('rec.action'));
    const want = select.value || store.get(LABEL_KEY);
    if (select.options.length !== rec.labels.length || [...select.options].some((o, i) => o.textContent !== t('rec.l.' + rec.labels[i]))) {
      select.replaceChildren(...rec.labels.map(l => { const o = document.createElement('option'); o.value = l; o.textContent = t('rec.l.' + l); return o; }));
      if (want && rec.labels.includes(want)) select.value = want;
    }
    select.disabled = rec.active;
    btn.textContent = t(rec.active ? 'rec.stop' : 'rec.start');
    btn.classList.toggle('is-recording', rec.active);
    panel.classList.toggle('is-active', rec.active);
    status.textContent = recError ? t('rec.fail', { msg: recError })
      : rec.active ? t('rec.active', { name: rec.name, s: Math.round(rec.seconds || 0) })
      : rec.last ? t('rec.saved', { name: rec.last }) : t('rec.idle');
  }
  async function pollRec() {
    try { rec = await api('/api/record'); recError = ''; } catch (err) { /* backend down: keep last state */ }
    paintRec();
  }
  select.addEventListener('change', () => store.set(LABEL_KEY, select.value));
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const body = rec.active ? { action: 'stop' } : { action: 'start', label: select.value };
    try {
      const r = await fetch('/api/record', { method: 'POST', headers: { 'X-Radar-Action': 'record', 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await r.json();
      if (!r.ok) throw Error(data.error || ('HTTP ' + r.status));
      rec = data; recError = '';
      if (!rec.active) chime();
    } catch (err) { recError = err && err.message ? err.message : String(err); }
    btn.disabled = false; paintRec();
  });
  setInterval(pollRec, 1000);
  pollRec();

  // Repaint our text when the language changes (app.js handles its own).
  for (const id of ['#langZh', '#langEn']) { const b = $(id); if (b) b.addEventListener('click', () => { paintSound(); paintRec(); }); }
  paintSound();
})();
