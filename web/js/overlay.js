import {
  api,
  avatarEl,
  createCueSequencer,
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

// The single FIFO lane every reveal cue runs on. A cue's animation starts at the
// moment its own sound starts, and only after the previous cue's sound has ended
// -- waiting on the clip itself, not on a guessed delay, is what keeps the
// toast, the correct-answer reveal and the rank swap from stacking.
const cues = createCueSequencer();
// The active round, and the epoch its queued cues are validated against. The
// epoch bumps whenever the round changes or the show finalizes, so a task still
// waiting its turn can tell it belongs to a moment now gone and drop itself
// instead of painting under the next round or the final podium.
let activeRoundId = null;
let cueEpoch = 0;

// The rank panel shows the podium contenders, not the whole field.
const RANK_SIZE = 4;
// Must equal `.o-rank-row` height (54) + `.o-rank-list` gap (8) in overlay.css:
// the swap moves a row by whole positions, so a CSS change on one side without
// the other would leave every row off by a slice of a row.
const RANK_PITCH = 62;

// The round whose ranking was last scheduled. This is the flicker guard for the
// swap: the 1s poll must not replay the animation for a reveal already drawn.
let rankRoundId = null;
// The order the last swap drew FROM. Null means the first reveal, where there
// is nothing to animate from.
let prevRankIds = null;

/**
 * The overlay has no UI, so it resolves the show itself:
 * ?show_id=... pins it, otherwise the newest show wins.
 */
let waitingRoundId = null;
let waitingTimer = null;

// The round whose correct-answer cue was already scheduled. The 1s poll reruns
// `render()`, so this is what keeps the cue to once per reveal.
let revealedRoundId = null;
// The round whose reveal VISUAL has actually been armed. A revealed round is not
// painted until its cue's turn on the queue arrives, so before arming it looks
// like a closed round rather than spoiling the answer ahead of the sound.
let revealArmedRoundId = null;

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
    // The beep must never queue behind a reveal: it only fires while the cue
    // lane is idle, so it can never delay the toast/correct/ranking sequence.
    if (!cues.busy) {
      playRandomWaitingSound();
    }
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
 * The cue fires on EVERY reveal, winner or not. It is queued behind the toast,
 * so with a winner it starts only once the jingle's sound has ended; with nobody
 * correct the queue is empty and it starts immediately. Either way the reveal
 * visual is armed at that same moment -- the highlight, the `Correcta:` line and
 * the answer avatars all appear together with the sound, never before it.
 * `revealedRoundId` is the flicker guard, set synchronously so the 1s poll does
 * not queue the same cue again.
 */
function renderCorrectCue(round) {
  const reveal = !!round && round.status === "revealed" && !!round.correct_option;
  if (!reveal) {
    stopCorrectCue();
    return;
  }
  if (revealedRoundId === round.id) return;
  revealedRoundId = round.id;
  const epoch = cueEpoch;
  cues.run(async () => {
    if (epoch !== cueEpoch) return;
    revealArmedRoundId = round.id;
    // Re-render now that the round is armed: this is the paint that flips the
    // board from "closed" to the correct-answer state, in step with the sound.
    render(round);
    await playRandomCorrectSound();
  });
}

function stopCorrectCue() {
  revealArmedRoundId = null;
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

  // A new round on screen invalidates the previous round's queued cues: bumping
  // the epoch makes any still-waiting toast/correct/ranking task drop itself.
  if (view.round.id !== activeRoundId) {
    activeRoundId = view.round.id;
    cueEpoch += 1;
  }

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
  // The guard is set synchronously, at schedule time: the card is built later,
  // on the cue lane, but the 1s poll must not queue a second toast in between.
  announcedKey = key;
  const epoch = cueEpoch;
  cues.run(async () => {
    if (epoch !== cueEpoch) return;

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
    // The entrance animation and its push sound are one cue: both start here,
    // and the next cue waits for this sound to finish.
    await playRandomPushSound();
  });
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
 * nothing to trade. It is queued behind the correct-answer cue, so the swap runs
 * after that sound has ended rather than on a guessed delay. `rankRoundId` is
 * the flicker guard, set synchronously since `render()` reruns every second.
 */
function renderRanking(round) {
  if (!round || round.status !== "revealed") {
    hideRanking();
    return;
  }
  if (rankRoundId === round.id) return;
  rankRoundId = round.id;
  hideRanking();
  const epoch = cueEpoch;
  cues.run(async () => {
    if (epoch !== cueEpoch) return;
    await showRanking();
  });
}

function hideRanking() {
  $("o-rank").classList.add("hidden");
}

/**
 * Fetch and reveal the leaderboard. Deliberately has no catch that surfaces an
 * error: a ranking that cannot be fetched is not worth a broken overlay, so it
 * simply stays hidden and the next reveal tries again. The swap animation and
 * its push sound are one cue -- they start together here, once the fetch has
 * succeeded, and the caller's await makes the lane wait for that sound.
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
  await playRandomPushSound();
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
  // Bumping the epoch invalidates every cue still waiting its turn: the podium
  // must be silent, so a queued toast/correct/ranking task no-ops when it wakes.
  // This runs before the `finalRendered` short circuit because the show ending
  // is what retires the cues, not whether the ranking fetch succeeded.
  cueEpoch += 1;
  // The podium covers the whole stage, so a toast from the last question would
  // print underneath it.
  hideFirstCorrect();
  // The podium retires the rank panel too. The guards are reset here -- not
  // merely hidden -- so the NEXT show announces and swaps from a clean slate:
  // a stale round id or a stale toast key would silence its first reveal.
  hideRanking();
  rankRoundId = null;
  prevRankIds = null;
  stopWaitingCue();
  stopCorrectCue();
  revealArmedRoundId = null;
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
  // The answer is only painted once the reveal cue's turn has come (armed), so a
  // revealed-but-unarmed round looks closed -- "Votación cerrada", no avatars --
  // until its sound starts. That is what keeps the visual from spoiling the cue.
  const armed = round.status === "revealed" && !!round.correct_option && revealArmedRoundId === round.id;
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

    if (armed) {
      opt.classList.add(letter === round.correct_option ? "is-correct" : "is-dimmed");
    }

    const first = firsts[letter];
    if (armed && first) {
      const img = avatarEl(first, "");
      img.classList.add("o-first");
      opt.append(img);
    }
    list.append(opt);
  });

  $("o-status").textContent =
    round.status === "open"
      ? "¡Votando en el chat! Respondé A, B, C o D"
      : armed
        ? `Correcta: ${round.correct_option}`
        : "Votación cerrada";

  // Enqueued in chain order: the toast owns the moment, the correct cue follows
  // it, the ranking swap comes last. The FIFO lane is the sequencing.
  renderFirstCorrect(round);
  renderCorrectCue(round);
  renderRanking(round);
}

setInterval(() => {
  refresh().catch(() => {});
}, 1000);
refresh();
