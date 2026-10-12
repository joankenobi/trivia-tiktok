import {
  api,
  avatarEl,
  currentRound,
  LETTERS,
  formatSeconds,
  remainingSeconds,
  roomFillPlan,
} from "/js/common.js";

const $ = (id) => document.getElementById(id);
let showId = null;
let roundId = null;
let pollTimer = null;
// The round on screen, kept for the countdown between polls. Named apart from
// the imported `currentRound()` helper.
let activeRound = null;

$("btn-create").onclick = async () => {
  const body = {
    room_id: $("room-id").value.trim(),
    question_count: Number($("qcount").value) || 10,
    question_time_limit_s: Number($("tlimit").value) || 20,
    // Creating a show starts it. The runner task owns the pacing from here.
    autoplay: true,
  };
  $("setup-msg").textContent = "";
  try {
    const show = await api("/shows", { method: "POST", body: JSON.stringify(body) });
    showId = show.id;
    $("setup").classList.add("hidden");
    $("board").classList.remove("hidden");
    $("round-total").textContent = show.rounds.length;
    await refresh();
  } catch (e) {
    $("setup-msg").textContent = e.message;
  }
};

/**
 * Offer the room id of the live that started today, so the host does not read
 * it off the stream. A suggestion only: the backend returns nulls when there is
 * no live, when it is from a previous day, or when upstream is down, and all
 * three leave the field empty for manual entry. An existing value is never
 * overwritten, so a re-render cannot discard what the host already typed.
 */
async function prefillRoomId() {
  const field = $("room-id");
  let live;
  try {
    live = await api("/lives/active");
  } catch {
    return;
  }
  if (!live?.room_id || field.value.trim()) return;

  field.value = live.room_id;
  $("room-hint").textContent = `En vivo: ${live.host_username || live.room_id}${sinceLabel(live.started_at)}`;
}

// " desde las 20:51", shared so both room hints read the same way.
function sinceLabel(startedAt) {
  return startedAt
    ? ` desde las ${new Date(startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : "";
}

/**
 * Place the currently active room id on one click, over whatever is in the field.
 *
 * A different intent from the auto-fill above, so it behaves differently in both
 * directions: the press overwrites a typed value (the host is stating which room
 * they are in, not asking for a default), and a live from a previous UTC day is
 * placed anyway with a warning -- at 20:00 local (UTC-3) the stream started at
 * 23:00 UTC, so after 21:00 the auto-fill has already given up on a show that is
 * still running. Placement wins; the warning is information.
 *
 * The ordering is load-bearing: assigning `value` fires no `input` event, so the
 * manual-edit listener below never sees this write and a hint set afterwards
 * survives. Every outcome says something -- a silent button reads as a broken
 * console.
 */
$("btn-fill-room").onclick = async () => {
  const btn = $("btn-fill-room");
  const hint = $("room-hint");
  btn.disabled = true;
  try {
    const live = await api("/lives/current");
    const plan = roomFillPlan(live);
    if (!plan.place) {
      // The route collapses "no live", "it ended" and "upstream is down" into one
      // null shape on purpose, so the message has to admit it cannot tell.
      hint.textContent = "No hay ningún directo activo ahora, o no se pudo consultar.";
      return;
    }
    $("room-id").value = plan.value;
    const who = live.host_username || plan.value;
    const since = sinceLabel(live.started_at);
    hint.textContent = plan.stale
      ? `En vivo desde ayer: ${who}${since}. El relleno automático omite los directos de ayer.`
      : `En vivo: ${who}${since}`;
  } catch (e) {
    // The upstream failure never reaches here -- the route degrades it to nulls --
    // so a throw means this console could not reach its own API.
    hint.textContent = `No se pudo consultar el directo activo: ${e.message}`;
  } finally {
    btn.disabled = false;
  }
};

// The hint describes the value in the field, so it must not outlive an edit.
$("room-id").addEventListener("input", () => { $("room-hint").textContent = ""; });
prefillRoomId();

$("btn-autoplay").onclick = async () => {
  // Toggle against the last known state, not a guess from the button label: the
  // runner may have finished the show on its own since the last poll.
  const on = await api(`/shows/${showId}`);
  await act(`/shows/${showId}/autoplay`, on.autoplay ? "DELETE" : "POST");
};

$("btn-next").onclick = async () => {
  await act(`/shows/${showId}/rounds`, "POST");
};
$("btn-close").onclick = async () => {
  if (!roundId) return;
  await act(`/rounds/${roundId}/close`, "POST");
};
$("btn-reveal").onclick = async () => {
  if (!roundId) return;
  await act(`/rounds/${roundId}/reveal`, "POST");
};

async function act(path, method) {
  try {
    await api(path, { method });
    await refresh();
  } catch (e) {
    alert(e.message);
  }
}

async function refresh() {
  if (!showId) return;
  const show = await api(`/shows/${showId}`);
  const open = show.rounds.find((r) => r.status === "open");
  const played = currentRound(show.rounds);
  // While nothing has played yet, fall back to the first round so the host sees
  // what is coming instead of an empty board. Close/reveal act on a round only.
  const shown = played || show.rounds[0];
  roundId = played ? played.id : null;
  if (!shown) return;

  activeRound = shown;
  $("round-pos").textContent = shown.position + 1;
  const st = $("round-status");
  st.textContent = shown.status;
  st.className = `pill ${shown.status === "open" ? "pill-open" : "pill-ok"}`;
  $("btn-next").disabled = !!open || show.rounds.every((r) => r.status !== "pending");
  $("btn-close").disabled = shown.status !== "open";
  $("btn-reveal").disabled = shown.status !== "closed";

  // Autoplay on a finished show has nothing left to drive, so the button is
  // disabled rather than accepting a POST that would 409.
  const autoBtn = $("btn-autoplay");
  autoBtn.textContent = show.autoplay ? "Detener automático" : "Reanudar automático";
  autoBtn.disabled = !show.autoplay && show.status === "finished";

  renderQuestion(shown);
  // The show payload already carries the live tally for the open round, so the
  // extra request is only a fallback for a round that has no tally at all.
  await renderTally(roundId, shown);
  await renderRanking();
  renderCountdown();
  $("conn").textContent = "en vivo";
  $("conn").className = "pill pill-ok";
}

/**
 * Draw the countdown from the server deadline. `remainingSeconds` re-anchors on
 * every poll, and this runs on the poll interval, so the number ticks without
 * drift even if the tab was backgrounded.
 */
function renderCountdown() {
  const el = $("countdown");
  const secs = remainingSeconds(activeRound);
  if (secs == null) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.textContent = `${formatSeconds(secs)}s`;
  el.className = `countdown ${secs <= 5 ? "countdown-urgent" : ""}`;
}

function renderQuestion(round) {
  $("question-text").textContent = round.question.text;
  const list = $("options");
  list.innerHTML = "";
  LETTERS.forEach((letter, i) => {
    const li = document.createElement("li");
    const badge = document.createElement("span");
    badge.className = `opt-letter opt-${letter}`;
    badge.textContent = letter;
    const text = document.createElement("span");
    text.className = "opt-text";
    text.textContent = round.question.options[i] ?? "";
    li.append(badge, text);

    if (round.correct_option) {
      if (letter === round.correct_option) li.classList.add("is-correct");
      else if (round.tally?.counts?.[letter] > 0) li.classList.add("is-wrong");
    }
    list.append(li);
  });

  const ex = $("explain");
  if (round.question.explanation) {
    ex.textContent = round.question.explanation;
    ex.classList.remove("hidden");
  } else {
    ex.classList.add("hidden");
  }
}

async function renderTally(id, round) {
  let tally = round.tally;
  if (id && !tally) {
    try {
      tally = await api(`/rounds/${id}/tally`);
    } catch { /* round may not exist yet */ }
  }
  tally = tally || { counts: {}, unique_voters: 0, first_by_option: {} };
  $("voters").textContent = tally.unique_voters;

  const total = Math.max(1, tally.unique_voters);
  const box = $("tally");
  box.innerHTML = "";
  LETTERS.forEach((letter) => {
    const n = tally.counts[letter] || 0;
    const row = document.createElement("div");
    row.className = "tally-row";

    const badge = document.createElement("span");
    badge.className = `opt-letter opt-${letter}`;
    badge.textContent = letter;

    const bar = document.createElement("div");
    bar.className = "tally-bar";
    const fill = document.createElement("div");
    fill.className = "tally-fill";
    fill.style.width = `${(n / total) * 100}%`;
    fill.style.background = `var(--${letter.toLowerCase()})`;
    bar.append(fill);

    const num = document.createElement("div");
    num.className = "tally-num";
    num.innerHTML = `${n} <small>${Math.round((n / total) * 100)}%</small>`;

    row.append(badge, bar, num);

    const first = tally.first_by_option[letter];
    if (first) row.append(avatarEl(first));
    box.append(row);
  });
}

async function renderRanking() {
  const body = $("rank-body");
  body.innerHTML = "";
  let rows = [];
  try {
    rows = await api(`/shows/${showId}/ranking`);
  } catch { return; }
  rows.slice(0, 20).forEach((r, i) => {
    const tr = document.createElement("tr");
    const who = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "who";
    wrap.append(avatarEl(r), Object.assign(document.createElement("span"), { textContent: r.nickname || r.unique_id }));
    who.append(wrap);
    tr.append(
      Object.assign(document.createElement("td"), { textContent: i + 1 }),
      who,
      Object.assign(document.createElement("td"), { className: "num", textContent: r.correct_count }),
      Object.assign(document.createElement("td"), { className: "num", textContent: r.points }),
    );
    body.append(tr);
  });
}

pollTimer = setInterval(() => { if (showId) refresh().catch(() => {}); }, 1000);
