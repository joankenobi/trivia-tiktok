import {
  api,
  avatarEl,
  currentRound,
  LETTERS,
  formatSeconds,
  remainingSeconds,
} from "/js/common.js";

const $ = (id) => document.getElementById(id);
let showId = null;
// The ranking is fetched only once the show is over; this guard keeps the 1s poll
// from re-requesting a result that can no longer change.
let finalRendered = false;

/**
 * The overlay has no UI, so it resolves the show itself:
 * ?show_id=... pins it, otherwise the newest show wins.
 */
async function discover() {
  const pinned = new URLSearchParams(location.search).get("show_id");
  if (pinned) {
    showId = Number(pinned);
    return true;
  }
  try {
    const active = await api("/shows/active");
    if (!active.show_id) return false;
    showId = active.show_id;
    return true;
  } catch {
    return false;
  }
}

async function refresh() {
  if (showId === null && !(await discover())) return;

  let show;
  try {
    show = await api(`/shows/${showId}`);
  } catch {
    return; // transient network error: keep the last frame on screen
  }

  const current = currentRound(show.rounds);
  if (!current) return;
  render(current);
  renderTimer(current);
  renderFinal(show);
}

/**
 * The countdown is anchored on the server deadline, so the overlay and the
 * runner agree on when the round shuts even if the OBS machine's clock drifts.
 */
function renderTimer(round) {
  const el = $("timer");
  const secs = remainingSeconds(round);
  if (secs == null) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  $("o-timer-value").textContent = formatSeconds(secs);
  el.classList.toggle("is-urgent", secs <= 5);
}

/**
 * Show the winner board once the runner marks the show finished. Autoplay means
 * nobody closes the browser to trigger a state change, so the last question
 * would otherwise just sit on screen forever.
 */
async function renderFinal(show) {
  const box = $("o-final");
  if (show.status !== "finished") {
    finalRendered = false;
    box.classList.add("hidden");
    return;
  }
  if (finalRendered) return;
  box.classList.remove("hidden");
  let rows = [];
  try {
    rows = await api(`/shows/${showId}/ranking`);
  } catch {
    return; // retry next tick instead of showing an empty podium
  }
  finalRendered = true;
  const list = $("o-final-list");
  list.innerHTML = "";
  rows.slice(0, 5).forEach((r) => {
    const li = document.createElement("li");
    const who = document.createElement("span");
    who.className = "who";
    who.append(avatarEl(r), Object.assign(document.createElement("span"), { textContent: r.nickname || r.unique_id }));
    li.append(who, Object.assign(document.createElement("span"), { className: "num", textContent: r.points }));
    list.append(li);
  });
}

function render(round) {
  $("q").textContent = round.question.text;

  const list = $("options");
  list.innerHTML = "";
  const reveal = !!round.correct_option;
  const counts = round.tally?.counts || {};
  const firsts = round.tally?.first_by_option || {};
  const total = Math.max(1, round.tally?.unique_voters || 1);

  LETTERS.forEach((letter, i) => {
    const n = counts[letter] || 0;
    const pct = Math.round((n / total) * 100);

    const opt = document.createElement("div");
    // Two-column grid: A and C sit in the left column, B and D in the right.
    // Badges pin to the outer edge, so they spread to the four corners.
    opt.className = "o-option";
    opt.dataset.side = i % 2 === 0 ? "left" : "right";

    const badge = document.createElement("span");
    badge.className = `opt-letter opt-${letter}`;
    badge.textContent = letter;

    const text = document.createElement("span");
    text.className = "opt-text";
    text.textContent = round.question.options[i] ?? "";

    const bar = document.createElement("div");
    bar.className = "o-bar";
    const fill = document.createElement("span");
    fill.style.width = `${pct}%`;
    fill.style.background = `var(--${letter.toLowerCase()})`;
    bar.append(fill);

    const count = document.createElement("span");
    count.className = "o-count";
    count.textContent = `${n} · ${pct}%`;

    opt.append(badge, text, bar, count);

    if (reveal) {
      opt.classList.add(letter === round.correct_option ? "is-correct" : "is-dimmed");
    }

    const first = firsts[letter];
    if (first) {
      const img = avatarEl(first, "");
      img.classList.add("o-first");
      opt.append(img);
    }
    list.append(opt);
  });

  $("o-status").textContent =
    round.status === "open"
      ? "¡Votando en el chat! Respondé A, B, C o D"
      : round.status === "closed"
        ? "Votación cerrada"
        : `Correcta: ${round.correct_option}`;
}

setInterval(() => {
  refresh().catch(() => {});
}, 1000);
refresh();
