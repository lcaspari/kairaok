// Songbook (library + add a song) and routing between songbook and stage.

import * as stage from "./stage.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

let songs = [];
let pollTimer = 0;

// ------------------------------------------------------------------ routing

function route() {
  const m = location.hash.match(/^#\/sing\/([0-9a-f]{10})$/);
  $("home").hidden = !!m;
  $("stage").hidden = !m;
  if (m) {
    clearTimeout(pollTimer);
    stage.open(m[1]);
  } else {
    stage.close();
    refresh();
  }
}
window.addEventListener("hashchange", route);

// ----------------------------------------------------------------- songbook

async function refresh() {
  clearTimeout(pollTimer);
  try {
    songs = await (await fetch("/api/songs")).json();
  } catch {
    return;                                         // server is restarting: keep what we have
  }
  render();
  if (songs.some((s) => s.status === "processing" || s.status === "queued") && !$("home").hidden) {
    pollTimer = setTimeout(refresh, 1500);
  }
}

function render() {
  const list = $("song-list");
  list.textContent = "";
  $("empty").hidden = songs.length > 0;
  songs.forEach((s, i) => {
    const row = el("li", "song");
    row.append(el("span", "song-no", String(songs.length - i).padStart(3, "0")));

    const main = el("div");
    main.append(el("div", "song-title", s.title || "New song"));
    if (s.status === "ready") {
      const bits = [s.artist, s.lyrics_source === "none" ? "no lyrics yet" : null].filter(Boolean);
      main.append(el("div", "song-sub", bits.join(", ")));
    } else if (s.status === "error") {
      main.append(el("div", "song-sub is-error", s.error || "Something went wrong while preparing this song."));
    } else {
      main.append(el("div", "song-sub", `${s.step_label || "Waiting in line"} (${Math.round((s.progress || 0) * 100)}%)`));
      const bar = el("div", "progress");
      const fill = el("i");
      fill.style.width = `${Math.round((s.progress || 0) * 100)}%`;
      bar.append(fill);
      main.append(bar);
    }
    row.append(main);

    const best = el("div", "song-best");
    if (s.best_score != null) {
      best.append(s.best_score.toLocaleString("en-US"), el("small", "", "best score"));
    }
    row.append(best);

    const actions = el("div", "song-actions");
    if (s.status === "ready") {
      const sing = el("a", "btn btn-primary", "Sing");
      sing.href = `#/sing/${s.id}`;
      actions.append(sing);
    }
    if (s.status !== "processing") {
      const rm = el("button", "btn btn-quiet song-remove", "Remove");
      rm.type = "button";
      rm.setAttribute("aria-label", `Remove ${s.title || "song"}`);
      rm.addEventListener("click", async () => {
        if (rm.dataset.armed !== "1") {             // second click confirms
          rm.dataset.armed = "1";
          rm.textContent = "Really remove?";
          setTimeout(() => { rm.dataset.armed = ""; rm.textContent = "Remove"; }, 3000);
          return;
        }
        await fetch(`/api/songs/${s.id}`, { method: "DELETE" });
        refresh();
      });
      actions.append(rm);
    }
    row.append(actions);
    list.append(row);
  });
}

// ---------------------------------------------------------------- add a song

$("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const error = $("add-error");
  error.hidden = true;
  const data = new FormData(form);
  const file = data.get("file");
  if (!data.get("url").trim() && !(file && file.size)) {
    error.textContent = "Paste a YouTube link or choose an audio file first.";
    error.hidden = false;
    return;
  }
  if (!(file && file.size)) data.delete("file");
  $("add-btn").disabled = true;
  try {
    const res = await fetch("/api/songs", { method: "POST", body: data });
    if (!res.ok) throw new Error((await res.json()).detail || "The song could not be added.");
    form.reset();
    form.querySelector("details").open = false;
    refresh();
  } catch (err) {
    error.textContent = err.message === "Failed to fetch"
      ? "Kairaok is not running. Start it again with run.sh." : err.message;
    error.hidden = false;
  } finally {
    $("add-btn").disabled = false;
  }
});

route();
