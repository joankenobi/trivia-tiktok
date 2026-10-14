import {
  api,
  avatarEl,
  correctCueDelayMs,
  firstCorrectKey,
  firstCorrectResponder,
  isWaitingForFirstAnswer,
  LETTERS,
  formatSeconds,
  pollView,
  rankOffsets,
  remainingSeconds,
  waitingCueIntervalMs,
} from "/js/common.js";
import {
  playRandomCorrectSound,
  playRandomPushSound,
  preloadCorrectSounds,
  preloadPushSounds,
  preloadWaitingSounds,
  playRandomWaitingSound,
} from "/js/sounds.js";

const $ = (id) => document.getElementById(id);
let showId = null;
// The ranking is fetched only once the show is over; this guard keeps the 1s poll
// from re-requesting a result that can no longer change.
let finalRendered = false;
// Identity of the winner currently announced. This is the flicker guard: the
// poll rebuilds `render()` every second, so the toast only rebuilds (and only
// animates) when this changes.
let announcedKey = null;

// The rank panel shows the podium contenders, not the whole field.
const RANK_SIZE = 4;
// Must equal `.o-rank-row` height (54) + `.o-rank-list` gap (8) in overlay.css:
// the swap moves a row by whole positions, so a CSS change on one side without
// the other would leave every row off by a slice of a row.
const RANK_PITCH = 62;
// The ranking lags the reveal toast so the two cues do not collide.
const RANK_DELAY_MS = 1200;

// The round whose ranking was last built. This is the flicker guard for the
// swap: the 1s poll must not replay the animation for a reveal already drawn.
let rankRoundId = null;
// The order the last swap drew FROM. Null means the first reveal, where there
// is nothing to animate from.
let prevRankIds = null;
let rankTimer = null;

/**
 * The overlay has no UI, so it resolves the show itself:
 * ?show_id=... pins it, otherwise the newest show wins.
 */
let waitingRoundId = null;
let waitingTimer = null;

// The round whose correct-answer cue was already scheduled. The 1s poll reruns
// `render()`, so this is what keeps the cue to once per reveal. `correctTimer`
// holds the delayed shot that follows the winner jingle.
let revealedRoundId = null;
let correctTimer = null;

function startWaitingCue(limitS) {
  if (waitingTimer) {
    clearInterval(waitingTimer);
    waitingTimer = null;
  }
  const interval = waitingCueIntervalMs(limitS);
  if (!interval || interval <= 0) {
    waitingTimer = null;
    return;
  }
  waitingTimer = setInterval(() => {
    playRandomWaitingSound();
  }, interval);
}

function stopWaitingCue() {
  if (waitingTimer) {
    clearInterval(waitingTimer);
    waitingTimer = null;
  }
  waitingRoundId = null;
}

function renderWaitingCue(round) {
  if (!isWaitingForFirstAnswer(round)) {
    stopWaitingCue();
    return;
  }
  if (waitingRoundId === round.id) {
    return;
  }
  waitingRoundId = round.id;
  startWaitingCue(round.question_time_limit_s);
}

/**
 * Play the correct-answer cue exactly once per revealed round.
 *
 * The cue fires on EVERY reveal, winner or not. With a winner the jingle lands
 * first and the cue waits `CORRECT_CUE_DELAY_MS` so the two never stack; with
 * nobody correct it fires immediately. `revealedRoundId` is the flicker guard:
 * the poll rebuilds `render()` every second and must not replay the cue.
 */
function renderCorrectCue(round) {
  const reveal = !!round && round.status === "revealed" && !!round.correct_option;
  if (!reveal) {
    stopCorrectCue();
    return;
  }
  if (revealedRoundId === round.id) return;
  revealedRoundId = round.id;
  stopCorrectCue();
  const delay = correctCueDelayMs(!!firstCorrectResponder(round));
  if (delay === 0) {
    playRandomCorrectSound();
    return;
  }
  correctTimer = setTimeout(() => {
    correctTimer = null;
    playRandomCorrectSound();
  }, delay);
}

function stopCorrectCue() {
  if (correctTimer !== null) {
    clearTimeout(correctTimer);
    correctTimer = null;
  }
}

async function discover() {
  // Warm the sound list and the Audio objects while the first polls run, so the
  // first reveal is not the request that fetches them.
  preloadPushSounds();
  preloadWaitingSounds();
  preloadCorrectSounds();
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

  const view = pollView(show);
  if (view.mode === "final") {
    // The podium owns the screen once the show ends. Redrawing the last round
    // here would re-run its reveal on every poll, retriggering the toast and
    // the rank swap -- each with its own sound cue -- about once per second.
    renderFinal(show);
    return;
  }
  if (view.mode === "idle") return;

  render(view.round);
  renderTimer(view.round);
  renderWaitingCue(view.round);
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
 * Announce the first correct responder, and only announce it once.
 *
 * Both numbers come from the tally the server already froze at close, so the
 * overlay never recomputes a score of its own. The card is rebuilt from scratch
 * on each announcement and left untouched on every poll in between: a fresh node
 * is what makes the entrance animation run again for the next question, with no
 * class toggling for the browser to coalesce away.
 */
function renderFirstCorrect(round) {
  const winner = firstCorrectResponder(round);
  if (!winner) {
    hideFirstCorrect();
    return;
  }

  const key = firstCorrectKey(round, winner);
  if (key === announcedKey) return;
  announcedKey = key;
  // A new toast is on screen: pair it with a sound cue. Fired here, not on
  // every poll, because this is the exact point the animation starts.
  playRandomPushSound();

  const card = document.createElement("div");
  card.className = "o-fc-card";

  const pic = avatarEl(winner, "");
  pic.classList.add("o-fc-avatar");

  const caption = document.createElement("span");
  caption.className = "o-fc-caption";
  caption.textContent = "¡Primero en acertar!";

  const name = document.createElement("span");
  name.className = "o-fc-name";
  name.textContent = winner.nickname || winner.unique_id;

  const points = document.createElement("span");
  points.className = "o-fc-points";
  points.textContent = `+${winner.points}`;

  const total = document.createElement("span");
  total.className = "o-fc-total";
  total.textContent = `Total ${winner.total_points}`;

  card.append(caption, pic, name, points, total);
  const box = $("o-first-correct");
  box.innerHTML = "";
  box.append(card);
  box.classList.remove("hidden");
}

function hideFirstCorrect() {
  // Forgetting the key is what guarantees the next announcement animates: an
  // equal key must always mean "the very same card is still on screen".
  announcedKey = null;
  const box = $("o-first-correct");
  box.classList.add("hidden");
  box.innerHTML = "";
}

/**
 * Decide, from the round alone, whether the rank panel is due.
 *
 * Only a revealed round qualifies ("solo tras el cartel"): before the answer is
 * on screen the ordering is not final, and on a pending or open round there is
 * nothing to trade. The reveal itself is delayed so the toast lands first;
 * `rankRoundId` is the flicker guard, since `render()` reruns every second.
 */
function renderRanking(round) {
  if (!round || round.status !== "revealed") {
    hideRanking();
    return;
  }
  if (rankRoundId === round.id) return;
  rankRoundId = round.id;
  hideRanking();
  rankTimer = setTimeout(() => {
    rankTimer = null;
    showRanking();
  }, RANK_DELAY_MS);
}

function hideRanking() {
  if (rankTimer !== null) {
    clearTimeout(rankTimer);
    rankTimer = null;
  }
  $("o-rank").classList.add("hidden");
}

/**
 * Fetch and reveal the leaderboard. Deliberately has no catch that surfaces an
 * error: a ranking that cannot be fetched is not worth a broken overlay, so it
 * simply stays hidden and the next reveal tries again.
 */
async function showRanking() {
  if (!showId) return;
  let rows;
  try {
    rows = await api(`/shows/${showId}/ranking`);
  } catch {
    return; // transient error: leave the panel hidden
  }
  if (!Array.isArray(rows) || rows.length === 0) return;
  $("o-rank").classList.remove("hidden");
  renderRankList(rows);
  playRandomPushSound();
}

/**
 * Draw the top rows, then animate them from where they were to where they are.
 *
 * The trick is two writes with a reflow between them: rows are laid out at the
 * offset they held in `prevRankIds`, the browser is forced to commit that (and
 * the freshly created nodes), and only then is the transform cleared so the CSS
 * transition has somewhere to travel from. Without the reflow both writes land
 * in the same style recalc and the swap does not animate.
 */
function renderRankList(rows) {
  const top = rows.slice(0, RANK_SIZE);
  const offsets = rankOffsets(prevRankIds, top, RANK_PITCH);
  const list = $("o-rank-list");
  list.innerHTML = "";

  top.forEach((row, i) => {
    const li = document.createElement("li");
    li.className = "o-rank-row";

    const pos = document.createElement("span");
    pos.className = "o-rank-pos";
    pos.textContent = i + 1;

    const pic = avatarEl(row, "");
    pic.classList.add("o-rank-avatar");

    const name = document.createElement("span");
    name.className = "o-rank-name";
    name.textContent = row.nickname || row.unique_id || "";

    const points = document.createElement("span");
    points.className = "o-rank-points";
    points.textContent = row.points;

    li.append(pos, pic, name, points);
    // Start at the OLD spot; the stagger keeps a multi-row shuffle readable.
    li.style.transform = `translateY(${offsets[i]}px)`;
    li.style.transitionDelay = `${i * 90}ms`;
    list.append(li);
  });

  void list.offsetHeight; // commit the starting transforms before clearing them
  list.querySelectorAll(".o-rank-row").forEach((el) => {
    el.style.transform = "";
  });

  // Remember this order as the origin of the NEXT swap.
  prevRankIds = top.map((r) => r.unique_id);
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
  // The podium covers the whole stage, so a toast from the last question would
  // print underneath it. It runs before the `finalRendered` short circuit
  // because the podium is what retires the announcement, not the fetch.
  hideFirstCorrect();
  // The podium retires the rank panel too. Both guards are reset here -- not
  // merely hidden -- so the NEXT show announces and swaps from a clean slate:
  // a stale round id or a stale toast key would silence its first reveal.
  hideRanking();
  rankRoundId = null;
  prevRankIds = null;
  stopWaitingCue();
  stopCorrectCue();
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

  renderFirstCorrect(round);
  renderRanking(round);
  renderCorrectCue(round);
}

setInterval(() => {
  refresh().catch(() => {});
}, 1000);
refresh();
