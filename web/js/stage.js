// The sing-along screen: playback, note highway, lyrics, microphone and score.

import { Mic } from "./mic.js";

const $ = (id) => document.getElementById(id);
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const COLORS = { ice: "#9be8ff", gold: "#ffd166", mint: "#5cf2b8", neon: "#ff4fa3", paper: "#f3ecff", violet: "#c4a1ff" };
// two singers: the first is drawn in gold, the second in violet
const SINGERS = [{ name: "Gold", color: COLORS.gold, paint: COLORS.mint }, { name: "Violet", color: COLORS.violet, paint: COLORS.violet }];
const fresh = () => [0, 1].map(() => ({ hit: 0, on: 0, first: null, segs: [] }));
const VIEW_SECONDS = 7;        // how much of the song fits across the highway
const NOW_AT = 0.24;           // where "now" sits, as a fraction of the width
const TOLERANCE = 1.0;         // semitones off that still count as a hit
const FREE_LEVEL = 0.07;       // how loud the mic must be to count on spoken/rapped notes
const FULL_MARKS_AT = 0.85;    // holding a note for 85% of its length scores it fully

const RANKS = [
  [9000, "Headliner"],
  [7500, "Crowd favourite"],
  [5500, "Solid set"],
  [3000, "Warm-up act"],
  [0, "Shower singer"],
];

// ------------------------------------------------------------- settings

const DEFAULTS = {
  volVocals: 0.15, volBand: 0.9, micVol: 1, reverb: 0.3, echo: 0.15,
  monitor: false, micDelay: 60, speakerMode: false, micDevice: "", mic2: "", lyricsPos: "below",
};
// Settings saved under the app's old name (kairaokay.*) are still read, so nothing is lost.
function loadSettings() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("kairaok.settings") || localStorage.getItem("kairaokay.settings") || "{}") }; }
  catch { return { ...DEFAULTS }; }
}
function saveSettings() {
  try { localStorage.setItem("kairaok.settings", JSON.stringify(settings)); } catch { /* private mode */ }
}
function loadOffset(id) {
  try { return parseFloat(localStorage.getItem(`kairaok.offset.${id}`) ?? localStorage.getItem(`kairaokay.offset.${id}`)) || 0; } catch { return 0; }
}
const settings = loadSettings();

// ----------------------------------------------------------------- state

let ctx = null;                // one AudioContext for the whole app
let master, vocalsGain, bandGain, mic, mic2;
let mics = [];
let S = null;                  // state of the song currently on stage
let raf = 0;

function ensureAudio() {
  if (ctx) return;
  ctx = new AudioContext({ latencyHint: "interactive" });
  master = new GainNode(ctx);
  master.connect(ctx.destination);
  vocalsGain = new GainNode(ctx, { gain: settings.volVocals });
  bandGain = new GainNode(ctx, { gain: settings.volBand });
  vocalsGain.connect(master);
  bandGain.connect(master);
  mic = new Mic(ctx, ctx.destination);
  mic2 = new Mic(ctx, ctx.destination);
  mics = [mic, mic2];
  for (const m of mics) {
    m.setVolume(settings.micVol);
    m.setReverb(settings.reverb);
    m.setEcho(settings.echo);
    m.setMonitor(settings.monitor);
  }
}

// ------------------------------------------------------------ open / close

export async function open(songId) {
  close();
  ensureAudio();
  const state = (S = {
    id: songId, song: null, buffers: null, sources: [],
    playing: false, t0: 0, pausedAt: 0,
    notes: [], sang: false,
    who: [0, 1].map(() => ({ trail: [], lastVoiceT: null, wasSound: false, sang: false })),
    lineKey: "", wordEls: [], lyricsOffset: loadOffset(songId),
    finished: false,
  });

  $("loading").hidden = false;
  $("loading").textContent = "Loading the song";
  $("result").hidden = true;
  $("play").disabled = true;
  $("seek").disabled = true;
  $("score").textContent = "0";
  $("score2").textContent = "0";
  $("line-now").textContent = "";
  $("line-next").textContent = "";
  $("st-title").textContent = "";
  $("st-artist").textContent = "";
  showNotice("");
  syncControls();
  raf = requestAnimationFrame(frame);
  startMic();                                      // asks for permission the first time

  try {
    const song = await (await fetch(`/api/songs/${songId}/data`)).json();
    if (S !== state) return;
    if (!song.notes) throw new Error(song.detail || "This song is not ready yet.");
    setSong(song);
    const [vocals, band] = await Promise.all(
      ["vocals.flac", "instrumental.flac"].map(async (name) => {
        const res = await fetch(`/media/${songId}/${name}`);
        if (!res.ok) throw new Error("The audio for this song is missing. Remove it and add it again.");
        return ctx.decodeAudioData(await res.arrayBuffer());
      })
    );
    if (S !== state) return;
    state.buffers = { vocals, band };
    state.duration = Math.max(vocals.duration, band.duration);
    $("dur").textContent = fmtTime(state.duration);
    $("seek").max = state.duration;
    $("seek").disabled = false;
    $("play").disabled = false;
    $("loading").hidden = true;
    if (!song.lyrics.lines.length) showNotice("No lyrics for this song yet. Open “Timing and lyrics” to add them.");
  } catch (err) {
    if (S === state) $("loading").textContent = err.message || "This song could not be loaded.";
  }
}

function setSong(song) {
  S.song = song;
  S.notes = song.notes.map((n) => ({ ...n, s: fresh() }));
  S.totalNoteTime = S.notes.reduce((a, n) => a + (n.end - n.start), 0) || 1;
  S.lineKey = "";
  $("st-title").textContent = song.title;
  $("st-artist").textContent = song.artist || "";
  $("ly-artist").value = song.artist || "";
  $("ly-title").value = song.title || "";
  $("lyrics-source").textContent = lyricsSummary(song.lyrics);
  document.title = `${song.title} · Kairaok`;
}

function lyricsSummary(ly) {
  const parts = [ly.source_label || "No lyrics"];
  if (ly.lines.length) parts.push(ly.word_level ? "timed word by word" : "timed line by line");
  return parts.join(", ") + "." + (ly.warnings?.length ? " " + ly.warnings.join(" ") : "");
}

export function close() {
  cancelAnimationFrame(raf);
  if (!S) return;
  discardTake();
  stopSources();
  for (const m of mics) m.stop();
  if ($("settings").open) $("settings").close();
  S = null;
  document.title = "Kairaok";
}

// ---------------------------------------------------------------- playback

function songTime() {
  if (!S) return 0;
  return S.playing ? ctx.currentTime - S.t0 : S.pausedAt;
}
// What the singer hears right now (the speakers lag a little behind the clock).
function heardTime() {
  const lag = S.playing ? ctx.outputLatency || ctx.baseLatency || 0 : 0;
  return clamp(songTime() - lag, 0, S.duration || 0);
}

function stopSources() {
  for (const src of S.sources) {
    src.onended = null;
    try { src.stop(); } catch { /* not started */ }
    src.disconnect();
  }
  S.sources = [];
}

async function play() {
  if (!S?.buffers || S.playing) return;
  if (S.finished || S.pausedAt >= S.duration - 0.1) restart();
  await ctx.resume();
  const from = S.pausedAt;
  const when = ctx.currentTime + 0.06;
  const state = S;
  S.sources = [[S.buffers.vocals, vocalsGain], [S.buffers.band, bandGain]].map(([buffer, out]) => {
    const src = new AudioBufferSourceNode(ctx, { buffer });
    src.connect(out);
    src.start(when, from);
    return src;
  });
  S.sources[1].onended = () => { if (S === state && S.playing) finish(); };
  S.t0 = when - from;
  S.playing = true;
  recordTake();
  for (const w of S.who) w.lastVoiceT = null;
  $("play").textContent = "Pause";
  $("play").setAttribute("aria-label", "Pause");
}

function pause() {
  if (!S?.playing) return;
  S.pausedAt = clamp(songTime(), 0, S.duration);
  stopSources();
  S.playing = false;
  if (S.rec?.state === "recording") S.rec.pause();
  $("play").textContent = "Play";
  $("play").setAttribute("aria-label", "Play");
}

function seek(t) {
  if (!S?.buffers) return;
  const was = S.playing;
  pause();
  S.pausedAt = clamp(t, 0, S.duration);
  S.finished = false;
  // what lies ahead will be sung again: forget its marks
  for (const n of S.notes) {
    if (n.end <= S.pausedAt) continue;
    for (const m of n.s) {
      m.segs = m.segs.filter((seg) => seg[1] <= S.pausedAt);
      m.hit = m.segs.reduce((a, seg) => a + seg[1] - seg[0], 0);
      m.on = Math.min(m.on, Math.max(0, S.pausedAt - n.start));
      if (n.start >= S.pausedAt) m.first = null;
    }
  }
  for (const w of S.who) w.trail = w.trail.filter((p) => p.t <= S.pausedAt);
  if (was) play();
}

function restart() {
  S.pausedAt = 0;
  S.finished = false;
  S.sang = false;
  for (const w of S.who) { w.trail = []; w.sang = false; }
  for (const n of S.notes) n.s = fresh();
  discardTake();
  $("result").hidden = true;
}

// ------------------------------------------------------------------ scoring

function score(k = 0) {
  let sum = 0;
  for (const n of S.notes) {
    const dur = n.end - n.start;
    sum += Math.min(1, n.s[k].hit / (dur * FULL_MARKS_AT)) * dur;
  }
  return Math.round((10000 * sum) / S.totalNoteTime);
}

// Melody: of the time you were singing on sung notes, how much was on pitch.
// Rhythm: did you come in on time for each note, and stay with it.
function parts(k = 0, upTo = Infinity) {
  let hit = 0, on = 0, rhythm = 0, count = 0;
  for (const n of S.notes) {
    if (n.start > upTo) break;
    const dur = n.end - n.start, m = n.s[k];
    if (!n.free) { hit += Math.min(m.hit, dur); on += Math.min(m.on, dur); }
    const late = m.first === null ? Infinity : Math.abs(m.first - n.start);
    const entry = late <= 0.1 ? 1 : Math.max(0, 1 - (late - 0.1) / 0.25);
    rhythm += 0.5 * entry + 0.5 * Math.min(1, m.on / (dur * 0.7));
    count++;
  }
  return {
    melody: on > 0.5 ? Math.min(1, hit / on) : null,
    rhythm: count ? rhythm / count : null,
  };
}
const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)}%`);
const duo = () => !!mic2?.active;

function noteAt(t) {
  const notes = S.notes;
  let lo = 0, hi = notes.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;                                   // last note starting at or before t
}

// Singers pick their own octave: compare pitches modulo the octave.
function foldTo(midi, target) {
  return midi + 12 * Math.round((target - midi) / 12);
}

function listen(heard) {
  for (let k = 0; k < mics.length; k++) listenTo(k, heard);
}

function listenTo(k, heard) {
  const m = mics[k], w = S.who[k];
  const reading = m.active ? m.read() : { midi: null, level: 0 };
  $(k ? "mic2-level" : "mic-level").firstElementChild.style.width = `${Math.round(reading.level * 100)}%`;
  if (!m.active || !S.playing || !S.notes.length) { w.lastVoiceT = null; return; }

  const t = heard - settings.micDelay / 1000;     // the moment this sound was sung for
  const dt = w.lastVoiceT === null ? 0 : clamp(t - w.lastVoiceT, 0, 0.1);
  w.lastVoiceT = t;
  const i = noteAt(t);
  const cur = i >= 0 && S.notes[i].end > t ? S.notes[i] : null;
  const mine = cur ? cur.s[k] : null;
  const mark = () => {
    mine.hit += dt;
    const last = mine.segs[mine.segs.length - 1];
    if (last && t - dt - last[1] < 0.06) last[1] = t;
    else mine.segs.push([Math.max(cur.start, t - dt), t]);
  };
  // Rhythm bookkeeping: when did the singer start making sound for each note,
  // and for how much of the note were they audible (whatever the pitch)?
  const sound = reading.midi !== null || reading.level > FREE_LEVEL;
  if (sound) {
    if (cur) {
      mine.on += dt;
      if (mine.first === null) mine.first = w.wasSound ? cur.start : t;
    } else if (!w.wasSound) {
      const next = S.notes[i + 1];                // came in slightly early
      if (next && next.s[k].first === null && next.start - t < 0.2) next.s[k].first = t;
    }
  }
  w.wasSound = sound;
  // spoken or rapped passages have no tune to hit: being heard on time is what counts
  if (cur?.free && dt > 0 && reading.level > FREE_LEVEL) { w.sang = S.sang = true; mark(); }
  if (reading.midi === null) return;
  w.sang = S.sang = true;
  // outside a note, draw the voice near the closest note so it stays on screen
  let ref = cur;
  if (!ref) {
    const prev = S.notes[i], next = S.notes[i + 1];
    ref = !prev ? next : !next ? prev : (t - prev.end < next.start - t ? prev : next);
  }
  const midi = foldTo(reading.midi, ref.midi);
  w.trail.push({ t, midi });
  if (w.trail.length > 2000) w.trail.splice(0, 500);

  if (cur && !cur.free && dt > 0 && Math.abs(midi - cur.midi) <= TOLERANCE) mark();
}

async function finish() {
  pause();
  S.pausedAt = S.duration;
  S.finished = true;
  const two = duo() && S.who[1].sang;
  const totals = [score(0), two ? score(1) : 0];
  const lead = two && totals[1] > totals[0] ? 1 : 0;
  const total = totals[lead];
  const accuracy = S.notes.reduce((a, n) => a + Math.min(n.s[lead].hit, n.end - n.start), 0) / S.totalNoteTime;
  if (!S.sang) {
    $("result-rank").textContent = "That's the song";
    $("result-score").parentElement.hidden = true;
    $("result-parts").hidden = true;
    discardTake();
    $("result-detail").textContent = mic?.active
      ? "The microphone did not pick up any singing, so there is no score this time."
      : "Turn on a microphone to see your pitch and get a score.";
  } else {
    const fmt = (x) => x.toLocaleString("en-US");
    $("result-rank").textContent = !two ? RANKS.find(([min]) => total >= min)[1]
      : totals[0] === totals[1] ? "A tie" : `${SINGERS[lead].name} wins`;
    $("result-score").parentElement.hidden = false;
    $("result-score").textContent = two ? `${fmt(totals[0])} : ${fmt(totals[1])}` : fmt(total);
    $("result-of").hidden = two;
    const p = parts(0), q = two ? parts(1) : null;
    $("result-parts").hidden = false;
    $("part-melody").textContent = two ? `${pct(p.melody)} : ${pct(q.melody)}` : pct(p.melody);
    $("part-rhythm").textContent = two ? `${pct(p.rhythm)} : ${pct(q.rhythm)}` : pct(p.rhythm);
    checkText();                                    // fills in "Lyrics" when the server has listened
    let detail = two ? "Gold on the left, violet on the right."
      : `You hit ${Math.round(accuracy * 100)}% of the notes' total length.`;
    try {
      const res = await fetch(`/api/songs/${S.id}/score`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ score: total, accuracy }),
      });
      const best = await res.json();
      detail += best.is_best ? " That is your best on this song."
        : ` Your best on this song is ${Number(best.best_score).toLocaleString("en-US")}.`;
    } catch { /* score is still shown */ }
    $("result-detail").textContent = detail;
  }
  $("result").hidden = false;
  $("again").focus();
}

// ------------------------------------------------------------------- lyrics

function updateLyrics(heard) {
  const lines = S.song?.lyrics.lines || [];
  const now = $("line-now"), next = $("line-next");
  if (!lines.length) {
    if (S.lineKey !== "none") { now.textContent = ""; next.textContent = ""; S.lineKey = "none"; }
    return;
  }
  const t = heard - S.lyricsOffset;
  let cur = -1;
  for (let lo = 0, hi = lines.length - 1; lo <= hi;) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].start <= t) { cur = mid; lo = mid + 1; } else hi = mid - 1;
  }
  const singing = cur >= 0 && t < lines[cur].end + 0.4;
  const shown = singing ? cur : cur + 1;          // while waiting, show what comes next
  const key = `${shown}:${singing}`;

  if (key !== S.lineKey) {
    S.lineKey = key;
    now.textContent = "";
    S.wordEls = [];
    now.classList.toggle("is-waiting", !singing);
    const line = lines[shown];
    if (line) {
      S.dots = document.createElement("span");
      S.dots.className = "dots";
      S.dots.setAttribute("aria-hidden", "true");
      now.append(S.dots);
      line.words.forEach((w, k) => {
        const el = document.createElement("span");
        el.className = "w";
        el.textContent = w.text;
        now.append(el, k < line.words.length - 1 ? " " : "");
        S.wordEls.push(el);
      });
    }
    next.textContent = lines[shown + 1]?.text || "";
  }

  const line = lines[shown];
  if (!line) return;
  if (singing) {
    S.dots.textContent = "";
    line.words.forEach((w, k) => {
      const f = clamp((t - w.start) / Math.max(0.05, w.end - w.start), 0, 1);
      S.wordEls[k].style.setProperty("--f", f.toFixed(3));
    });
  } else {
    // count in with dots after a long break, the way karaoke screens do
    const wait = line.start - t;
    const since = cur >= 0 ? t - lines[cur].end : t;
    const n = wait <= 3 && since + wait > 4 ? Math.ceil(wait) : 0;
    const dots = "●".repeat(clamp(n, 0, 3));
    if (S.dots.textContent !== dots) S.dots.textContent = dots;
  }
}

// ------------------------------------------------------------------ highway

function roundRect(g, x, y, w, h) {
  const r = Math.min(h / 2, w / 2);
  g.beginPath();
  g.roundRect(x, y, w, h, r);
}

function draw(heard) {
  const canvas = $("highway");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  if (!S?.song || !W || !H) return;

  const pps = W / VIEW_SECONDS;
  const nowX = W * NOW_AT;
  const x = (t) => nowX + (t - heard) * pps;
  const tLeft = heard - nowX / pps, tRight = heard + (W - nowX) / pps;

  const [lo, hi] = S.song.range;
  const low = lo - 3, rows = hi - lo + 7;
  const rowH = H / rows;
  const y = (midi) => H - (clamp(midi, low - 0.5, low + rows - 0.5) - low + 0.5) * rowH;
  const barH = clamp(rowH * 0.92, 7, 22);

  // pitch rows: a faint staff, slightly stronger on every C
  g.lineWidth = 1;
  for (let m = low; m < low + rows; m++) {
    g.strokeStyle = m % 12 === 0 ? "rgba(243,236,255,0.10)" : "rgba(243,236,255,0.035)";
    const yy = Math.round(y(m)) + 0.5;
    g.beginPath(); g.moveTo(0, yy); g.lineTo(W, yy); g.stroke();
  }

  // beats and bar lines
  const downs = new Set(S.song.downbeats);
  for (const b of S.song.beats) {
    if (b < tLeft) continue;
    if (b > tRight) break;
    const strong = downs.has(b);
    g.strokeStyle = strong ? "rgba(243,236,255,0.20)" : "rgba(243,236,255,0.07)";
    const xx = Math.round(x(b)) + 0.5;
    g.beginPath(); g.moveTo(xx, 0); g.lineTo(xx, H); g.stroke();
  }

  // the melody
  const first = Math.max(0, noteAt(tLeft));
  for (let i = first; i < S.notes.length; i++) {
    const n = S.notes[i];
    if (n.start > tRight) break;
    if (n.end < tLeft) continue;
    const x1 = x(n.start), w = Math.max(barH * 0.6, (n.end - n.start) * pps - 2);
    const yy = y(n.midi) - barH / 2;
    const active = n.start <= heard && n.end > heard;
    roundRect(g, x1, yy, w, barH);
    if (n.free) {
      // spoken or rapped: outlined in pink, any pitch counts
      g.fillStyle = n.end < heard ? "rgba(255,79,163,0.08)" : "rgba(255,79,163,0.16)";
      g.fill();
      g.strokeStyle = n.end < heard ? "rgba(255,79,163,0.35)" : "rgba(255,79,163,0.8)";
      g.lineWidth = 1.5; g.setLineDash([5, 4]); g.stroke(); g.setLineDash([]);
    } else {
      g.fillStyle = n.end < heard ? "rgba(155,232,255,0.16)" : active ? "rgba(155,232,255,0.42)" : "rgba(155,232,255,0.30)";
      g.fill();
    }
    if (n.s[0].segs.length || n.s[1].segs.length) {
      g.save();
      g.clip();                                    // keep the hit paint inside the bar
      const two = duo();                           // two singers share the bar: upper and lower half
      n.s.forEach((m, k) => {
        g.fillStyle = SINGERS[k].paint;
        const top = two ? yy + (k * barH) / 2 : yy, h = two ? barH / 2 : barH;
        for (const [a, b] of m.segs) g.fillRect(x(a), top, Math.max(1.5, (b - a) * pps), h);
      });
      g.restore();
    }
    if (active && !n.free) {
      roundRect(g, x1, yy, w, barH);
      g.strokeStyle = COLORS.ice; g.lineWidth = 1.5; g.stroke();
    }
  }

  if (settings.lyricsPos === "notes") drawWords(g, heard, x, y, barH, tLeft, tRight, H);

  // now line
  g.strokeStyle = "rgba(255,209,102,0.55)";
  g.lineWidth = 2;
  g.beginPath(); g.moveTo(nowX, 0); g.lineTo(nowX, H); g.stroke();

  // the voices
  S.who.forEach((w, k) => drawTrail(g, w.trail, SINGERS[k].color, heard, x, y, barH, tLeft));
}

function drawTrail(g, trail, color, heard, x, y, barH, tLeft) {
  if (!trail.length) return;
  g.strokeStyle = color;
  g.lineWidth = Math.max(3, barH * 0.38);
  g.lineCap = "round"; g.lineJoin = "round";
  let open = false, prev = null;
  g.beginPath();
  for (let i = trail.length - 1; i >= 0; i--) {
    const p = trail[i];
    if (p.t < tLeft) break;
    if (p.t > heard + 0.5) continue;
    const joined = prev && prev.t - p.t < 0.09 && Math.abs(prev.midi - p.midi) < 1.6;
    if (joined) g.lineTo(x(p.t), y(p.midi));
    else {
      if (open && prev) g.lineTo(x(prev.t) + 0.01, y(prev.midi));   // lone points still show as dots
      g.moveTo(x(p.t), y(p.midi));
      open = true;
    }
    prev = p;
  }
  if (open && prev) g.lineTo(x(prev.t) + 0.01, y(prev.midi));
  g.stroke();

  const head = trail[trail.length - 1];
  if (head && S.playing && heard - head.t < 0.25) {
    g.fillStyle = color;
    g.beginPath(); g.arc(x(head.t), y(head.midi), Math.max(5, barH * 0.5), 0, Math.PI * 2); g.fill();
  }
}

// Lyrics on the highway: every word sits right above the note it is sung on.
function drawWords(g, heard, x, y, barH, tLeft, tRight, H) {
  const lines = S.song.lyrics.lines;
  const off = S.lyricsOffset;
  const size = clamp(H * 0.036, 14, 22);
  const lineH = size * 1.15;
  g.font = `600 ${size}px Figtree, system-ui, sans-serif`;
  g.textBaseline = "alphabetic";
  let lastRight = -Infinity, level = 0, lastBase = H * 0.3;
  for (const line of lines) {
    if (line.end + off < tLeft - 2) continue;
    if (line.start + off > tRight) break;
    for (const w of line.words) {
      const ws = w.start + off;
      if (ws > tRight) break;
      const xx = x(ws);
      const width = g.measureText(w.text).width;
      if (xx + width < 0) continue;
      const i = noteAt(ws + 0.03);
      let n = i >= 0 && S.notes[i].end > ws - 0.05 ? S.notes[i] : null;
      if (!n && S.notes[i + 1] && S.notes[i + 1].start - ws < 0.3) n = S.notes[i + 1];
      const base = n ? y(n.midi) - barH / 2 - 6 : lastBase;
      lastBase = base;
      level = xx < lastRight + 6 ? (level + 1) % 3 : 0;   // fast passages: stack words instead of overlapping
      const yy = Math.max(size + 2, base - level * lineH);
      g.fillStyle = ws <= heard ? COLORS.neon : COLORS.paper;
      g.globalAlpha = w.end + off < heard - 0.3 ? 0.55 : 1;
      g.fillText(w.text, xx, yy);
      g.globalAlpha = 1;
      lastRight = xx + width;
    }
  }
}

// --------------------------------------------------------------- frame loop

function frame() {
  raf = requestAnimationFrame(frame);
  if (!S) return;
  const heard = heardTime();
  listen(heard);
  draw(heard);
  if (S.song) {
    updateLyrics(heard);
    if (S.buffers) {
      $("time").textContent = fmtTime(heard);
      if (!S.scrubbing) {
        $("seek").value = heard;
        $("seek").style.setProperty("--p", `${(100 * heard) / (S.duration || 1)}%`);
      }
      const sc = String(score().toLocaleString("en-US"));
      if ($("score").textContent !== sc) $("score").textContent = sc;
      const two = duo();
      if ($("score2").hidden === two) { $("score2").hidden = !two; $("score").classList.toggle("is-duo", two); }
      if (two) {
        const sc2 = String(score(1).toLocaleString("en-US"));
        if ($("score2").textContent !== sc2) $("score2").textContent = sc2;
      }
      if (S.playing && (S.frameNo = (S.frameNo || 0) + 1) % 20 === 0) {
        const p = S.who[0].sang ? parts(0, heard) : { melody: null, rhythm: null };
        $("live-melody").textContent = pct(p.melody);
        $("live-rhythm").textContent = pct(p.rhythm);
      }
    }
  }
}

// --------------------------------------------------------------- microphone

function showNotice(text, kind = "") {
  $("notice").dataset.kind = kind;
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

// The second singer can use another microphone, or the second input of the same
// audio interface (saved as "deviceId|1"). In that case singer one listens to
// input 1 only instead of both inputs added together.
function mic2Choice() {
  if (!settings.mic2) return null;
  const cut = settings.mic2.lastIndexOf("|");
  return { deviceId: settings.mic2.slice(0, cut), channel: settings.mic2.slice(cut + 1) === "1" ? 1 : "sum" };
}

async function startMic(deviceId = settings.micDevice) {
  const select = $("mic-device"), select2 = $("mic2-device");
  if (!navigator.mediaDevices?.getUserMedia) {
    showNotice("This browser cannot use the microphone here. Open Kairaok at http://127.0.0.1 in Chrome or Safari.");
    return;
  }
  try {
    const used = await mic.start(deviceId, settings.speakerMode);
    if (!S) { mic.stop(); return; }
    const devices = await Mic.devices();
    select.textContent = "";
    for (const d of devices) select.add(new Option(d.label || "Microphone", d.deviceId));
    select.add(new Option("Microphone off", "off"));
    select.value = devices.some((d) => d.deviceId === used) ? used : devices[0]?.deviceId || "off";
    settings.micDevice = select.value === "off" ? "" : select.value;

    select2.textContent = "";
    select2.add(new Option("Second singer: off", ""));
    for (const d of devices) {
      const label = d.label || "Microphone";
      select2.add(new Option(`Second singer: ${label}`, `${d.deviceId}|sum`));
      select2.add(new Option(`Second singer: ${label}, input 2`, `${d.deviceId}|1`));
    }
    select2.disabled = false;
    if (![...select2.options].some((o) => o.value === settings.mic2)) settings.mic2 = "";
    select2.value = settings.mic2;
    saveSettings();
    await startMic2();
    if ($("notice").dataset.kind === "mic") showNotice("");
  } catch (err) {
    mic2.stop();
    select.textContent = "";
    select.add(new Option("Microphone off", "off"));
    select.add(new Option("Turn on microphone", ""));
    select.value = "off";
    select2.disabled = true;
    showNotice(err.name === "NotAllowedError"
      ? "The microphone is blocked. Allow it in the browser's address bar to see your pitch and get a score."
      : "No microphone found. You can still sing along, just without pitch and score.", "mic");
  }
}

async function startMic2() {
  const choice = mic2Choice();
  mic2.stop();
  const shared = choice && choice.channel === 1 && choice.deviceId === settings.micDevice;
  mic.setChannel(shared ? 0 : "sum");
  if (!choice || !mic.active) return;
  try {
    await mic2.start(choice.deviceId, settings.speakerMode, choice.channel, true);
    if (!S) mic2.stop();
  } catch {
    settings.mic2 = "";
    $("mic2-device").value = "";
    saveSettings();
    mic.setChannel("sum");
    showNotice("The second microphone could not be opened.", "mic");
  }
}

// ------------------------------------------------------------------ controls

function syncControls() {
  $("vol-vocals").value = settings.volVocals;
  $("vol-band").value = settings.volBand;
  $("mic-vol").value = settings.micVol;
  $("reverb").value = settings.reverb;
  $("echo").value = settings.echo;
  $("monitor").checked = settings.monitor;
  $("mic-delay").value = settings.micDelay;
  $("echo-cancel").checked = settings.speakerMode;
  $("lyrics-offset").value = S ? S.lyricsOffset : 0;
  $("lyrics-pos").value = settings.lyricsPos;
  applyLyricsPos();
  showOutputs();
}
function showOutputs() {
  const off = parseFloat($("lyrics-offset").value);
  $("lyrics-offset-out").textContent = off === 0 ? "on time" : `${Math.abs(off).toFixed(2)} s ${off > 0 ? "later" : "earlier"}`;
  $("mic-delay-out").textContent = `${$("mic-delay").value} ms`;
}

function applyLyricsPos() {
  $("stage").classList.toggle("lyrics-above", settings.lyricsPos === "above");
  $("stage").classList.toggle("lyrics-notes", settings.lyricsPos === "notes");
}

function bind(id, key, apply) {
  $(id).addEventListener("input", (e) => {
    settings[key] = e.target.type === "checkbox" ? e.target.checked : parseFloat(e.target.value);
    saveSettings();
    if (ctx) apply(settings[key]);
    showOutputs();
  });
}

function init() {
  bind("vol-vocals", "volVocals", (v) => vocalsGain.gain.setTargetAtTime(v, ctx.currentTime, 0.02));
  bind("vol-band", "volBand", (v) => bandGain.gain.setTargetAtTime(v, ctx.currentTime, 0.02));
  bind("mic-vol", "micVol", (v) => mics.forEach((m) => m.setVolume(v)));
  bind("reverb", "reverb", (v) => mics.forEach((m) => m.setReverb(v)));
  bind("echo", "echo", (v) => mics.forEach((m) => m.setEcho(v)));
  bind("monitor", "monitor", (v) => { mics.forEach((m) => m.setMonitor(v)); ctx.resume(); });
  bind("mic-delay", "micDelay", () => {});
  bind("echo-cancel", "speakerMode", () => { if (S && mic.active) startMic(); });

  $("lyrics-pos").addEventListener("change", (e) => {
    settings.lyricsPos = e.target.value;
    saveSettings();
    applyLyricsPos();
  });

  $("lyrics-offset").addEventListener("input", (e) => {
    if (!S) return;
    S.lyricsOffset = parseFloat(e.target.value);
    try { localStorage.setItem(`kairaok.offset.${S.id}`, S.lyricsOffset); } catch { /* private mode */ }
    showOutputs();
  });

  $("mic-device").addEventListener("change", (e) => {
    if (e.target.value === "off") { mic.stop(); mic2.stop(); settings.micDevice = ""; saveSettings(); }
    else startMic(e.target.value);
  });
  $("mic2-device").addEventListener("change", (e) => {
    settings.mic2 = e.target.value;
    saveSettings();
    startMic2();
  });

  $("play").addEventListener("click", () => (S?.playing ? pause() : play()));
  $("again").addEventListener("click", () => { restart(); play(); });
  const seekEl = $("seek");
  seekEl.addEventListener("input", () => {
    if (!S) return;
    S.scrubbing = true;
    seekEl.style.setProperty("--p", `${(100 * seekEl.value) / (S.duration || 1)}%`);
  });
  seekEl.addEventListener("change", () => {
    if (!S) return;
    S.scrubbing = false;
    $("result").hidden = true;
    seek(parseFloat(seekEl.value));
  });

  document.addEventListener("keydown", (e) => {
    if (!S || $("stage").hidden || $("settings").open) return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (e.code === "Space") { e.preventDefault(); S.playing ? pause() : play(); }
    else if (e.code === "ArrowLeft") seek(songTime() - 5);
    else if (e.code === "ArrowRight") seek(songTime() + 5);
  });

  $("open-settings").addEventListener("click", () => {
    $("ly-status").textContent = "";
    syncControls();
    $("settings").showModal();
  });
  $("ly-search").addEventListener("click", () => redoLyrics("auto"));
  $("ly-own").addEventListener("click", () => redoLyrics("own"));
  $("ly-ear").addEventListener("click", () => redoLyrics("ear"));
  $("ly-transcribe").addEventListener("click", () => redoLyrics("transcribe"));
  $("redo-melody").addEventListener("click", redoMelody);
}

async function redoLyrics(mode) {
  if (!S?.song) return;
  const state = S;
  const status = $("ly-status");
  const buttons = ["ly-own", "ly-search", "ly-ear", "ly-transcribe"].map($);
  const own = $("ly-text").value.trim();
  if (mode === "own" && !own) {
    status.textContent = "Paste your lyrics into the box first.";
    $("ly-text").focus();
    return;
  }
  buttons.forEach((b) => (b.disabled = true));
  status.textContent = "Working on the lyrics. This can take a minute or two.";
  try {
    const res = await fetch(`/api/songs/${S.id}/lyrics`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        artist: $("ly-artist").value, title: $("ly-title").value,
        // "own" uses the pasted text, "ear" re-times whatever text the song has now,
        // "auto" searches online, "transcribe" ignores all text
        lyrics: mode === "own" ? own
          : mode === "ear" ? S.song.lyrics.lines.map((l) => l.text).join("\n") : "",
        mode: mode === "own" ? "auto" : mode,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || "The lyrics could not be updated.");
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      if (S !== state) return;
      const meta = await (await fetch(`/api/songs/${S.id}`)).json();
      if (meta.status !== "processing" && meta.status !== "queued") break;
      status.textContent = `${meta.step_label || "Working"} (${Math.round((meta.progress || 0) * 100)}%)`;
    }
    const song = await (await fetch(`/api/songs/${S.id}/data`)).json();
    if (S !== state) return;
    S.song.lyrics = song.lyrics;
    S.song.title = song.title; S.song.artist = song.artist;
    S.song.range = song.range;
    const old = new Map(S.notes.map((n) => [n.start, n]));   // notes are re-fitted to the new lyrics
    S.notes = song.notes.map((n) => ({ ...n, s: old.get(n.start)?.end === n.end ? old.get(n.start).s : fresh() }));
    S.totalNoteTime = S.notes.reduce((a, n) => a + (n.end - n.start), 0) || 1;
    S.lineKey = "";
    $("st-title").textContent = song.title;
    $("st-artist").textContent = song.artist || "";
    $("lyrics-source").textContent = lyricsSummary(song.lyrics);
    const warn = song.lyrics.warnings?.length ? " " + song.lyrics.warnings.join(" ") : "";
    status.textContent = (song.lyrics.lines.length
      ? `Done: ${song.lyrics.lines.length} lines. ${song.lyrics.source_label}.`
      : "No lyrics could be found or timed.") + warn;
    if (song.lyrics.lines.length && $("notice").dataset.kind !== "mic") showNotice("");
  } catch (err) {
    status.textContent = err.message;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

// ---- a recording of the microphone, checked against the lyrics after the song

function recordTake() {
  if (!mic?.active || !mic.stream || !window.MediaRecorder || !S.song?.lyrics.lines.length) return;
  if (S.rec && S.rec.stream !== mic.stream) discardTake();
  if (S.rec) { if (S.rec.state === "paused") S.rec.resume(); return; }
  try {
    const rec = new MediaRecorder(mic.stream, { audioBitsPerSecond: 64000 });
    S.takeChunks = [];
    S.takeFrom = S.pausedAt;
    rec.ondataavailable = (e) => { if (e.data.size) S?.takeChunks?.push(e.data); };
    rec.start(1000);
    S.rec = rec;
  } catch { S.rec = null; }
}

function discardTake() {
  if (!S) return;
  try { if (S.rec && S.rec.state !== "inactive") S.rec.stop(); } catch { /* already stopped */ }
  S.rec = null;
  S.takeChunks = [];
}

async function checkText() {
  const out = $("part-text");
  const state = S, rec = S.rec;
  out.classList.remove("is-pending");
  if (!rec) { out.textContent = "–"; return; }
  out.textContent = "listening…";
  out.classList.add("is-pending");
  try {
    await new Promise((resolve) => { rec.onstop = resolve; rec.state === "inactive" ? resolve() : rec.stop(); });
    const blob = new Blob(S.takeChunks, { type: rec.mimeType || "audio/webm" });
    const form = new FormData();
    form.append("audio", blob, /mp4|aac/.test(blob.type) ? "take.mp4" : "take.webm");
    form.append("start", String(S.takeFrom || 0));
    state.rec = null;
    const res = await (await fetch(`/api/songs/${state.id}/text-score`, { method: "POST", body: form })).json();
    if (S !== state) return;
    out.classList.remove("is-pending");
    out.textContent = res.available && res.score !== null ? pct(res.score) : "–";
    if (!res.available) out.title = "Needs WhisperX; not available right now.";
  } catch {
    if (S === state) { out.classList.remove("is-pending"); out.textContent = "–"; }
  }
}

async function redoMelody() {
  if (!S?.song) return;
  const state = S, id = S.id, status = $("melody-status");
  $("redo-melody").disabled = true;
  pause();
  status.textContent = "Tracing the melody again. This takes about a minute.";
  try {
    const res = await fetch(`/api/songs/${id}/melody`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json()).detail || "The melody could not be updated.");
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      if (S !== state) return;
      const meta = await (await fetch(`/api/songs/${id}`)).json();
      if (meta.status !== "processing" && meta.status !== "queued") break;
      status.textContent = `${meta.step_label || "Working"} (${Math.round((meta.progress || 0) * 100)}%)`;
    }
    status.textContent = "";
    $("settings").close();
    open(id);                                       // reload the song with its new notes
  } catch (err) {
    status.textContent = err.message;
  } finally {
    $("redo-melody").disabled = false;
  }
}

init();
