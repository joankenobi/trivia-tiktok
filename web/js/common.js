const API = "/api/v1";
const LETTERS = ["A", "B", "C", "D"];

/**
 * A countdown timer driven by the server's clock, not the local one.
 *
 * The server sends `deadline_epoch_ms` and `server_epoch_ms` for the same
 * instant. Anchoring on the difference means the countdown shows what the
 * server will actually enforce, even when the OBS machine's clock is off.
 * Re-anchoring on every poll absorbs the drift instead of accumulating it.
 */
export function remainingSeconds(round, now = Date.now()) {
  if (!round || !round.deadline_epoch_ms) return null;
  const serverNow = round.server_epoch_ms || now;
  const skew = serverNow - now;
  return Math.max(0, Math.round((round.deadline_epoch_ms - (now + skew)) / 1000));
}

export function formatSeconds(secs) {
  if (secs == null) return "—";
  return secs <= 0 ? "0" : String(secs);
}

/**
 * The round the audience is currently looking at.
 *
 * An open round wins. Otherwise it is the *last* round that already played, which
 * is the one holding its answer on screen between questions. Scanning backwards
 * is the whole point: a forward scan returns position 0, so the moment round 2
 * closes the overlay would jump back and show question 1 with its answer.
 * Returns undefined while every round is still pending.
 */
export function currentRound(rounds) {
  const ordered = [...rounds].sort((a, b) => a.position - b.position);
  return (
    ordered.find((r) => r.status === "open") ||
    [...ordered].reverse().find((r) => r.status !== "pending")
  );
}

/**
 * The viewer the reveal frame should announce: whoever answered the correct
 * option first.
 *
 * `first_by_option` holds the earliest answer per option regardless of
 * correctness, but one vote per round is enforced, so the entry for the correct
 * option IS the first correct responder. No correctness filter needed here.
 *
 * Only a revealed round announces: `closed` already stores the answer on the
 * server but the API only exposes it once the host reveals, and announcing a
 * round that is merely over would spoil it a second early.
 */
export function firstCorrectResponder(round) {
  if (!round || round.status !== "revealed" || !round.correct_option) return null;
  return round.tally?.first_by_option?.[round.correct_option] || null;
}

/**
 * Identity of the current announcement, and the only thing allowed to decide
 * that the toast animates.
 *
 * `render()` rebuilds its DOM on every 1s poll, so an entrance animation keyed on
 * "there is a winner" replays once per second and strobes on a live stream. The
 * round belongs in the key because the same viewer winning two questions in a row
 * is a new announcement, not the same one still on screen.
 */
export function firstCorrectKey(round, winner) {
  return `${round.id}:${winner.unique_id}`;
}

/**
 * What one press of the host's room-id button should do, as a plain decision.
 *
 * Lives here, not in `control.js`, for the same reason `currentRound` and
 * `firstCorrectResponder` do: control.js binds the DOM on import and cannot be
 * tested by plain node. Pure, so all three outcomes are checked without a
 * browser.
 *
 * The rule is that placement always wins. `stale` means the live started before
 * the current UTC day, which is exactly the case the passive auto-fill gives up
 * on -- a stream that began at 20:00 local (23:00 UTC) is still running after
 * 21:00 and the auto-fill has already written it off as yesterday. The host
 * pressing the button is asserting they are streaming, so the id goes in and the
 * warning rides along. Refusing would just hand back the bug the button was
 * added to fix.
 *
 * Refuses only what there is nothing to place: the route collapses no live, an
 * ended live and an unreadable upstream into the same null shape, and a refused
 * plan carries neither a value nor a warning so the console can say why.
 */
export function roomFillPlan(live) {
  const roomId = typeof live?.room_id === "string" ? live.room_id.trim() : "";
  if (!roomId) return { place: false, value: null, stale: false };
  return { place: true, value: roomId, stale: live.stale === true };
}

export async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

/**
 * Render a first-responder badge. avatar_path is nullable and the file may be
 * missing, so a broken image falls back to an initial rather than a broken icon.
 */
export function avatarEl(voter, cls = "avatar") {
  const initial = (voter.nickname || voter.unique_id || "?").trim().charAt(0).toUpperCase();
  const fallback = () => {
    const span = document.createElement("span");
    span.className = "avatar-fallback";
    span.textContent = initial;
    span.title = voter.nickname || voter.unique_id;
    return span;
  };
  if (!voter.avatar_path) return fallback();
  const img = document.createElement("img");
  img.className = cls;
  img.alt = voter.nickname || voter.unique_id;
  img.src = voter.avatar_path;
  img.onerror = () => img.replaceWith(fallback());
  return img;
}

/**
 * Where each row of a NEW ranking should start before it slides into place.
 *
 * The overlay animates the leaderboard by laying every row out at the spot it
 * held in the previous poll and then clearing the transform, so a CSS transition
 * carries it to its new spot. This is that starting offset in px:
 * `(oldIndex - newIndex) * pitch`. Positive means the row came from below.
 *
 * A row absent from `prevIds` is a newcomer with no past to move from, so it
 * starts where it already is (`0`). A null or empty `prevIds` is the first
 * reveal: every row is a newcomer and nothing animates.
 */
export function rankOffsets(prevIds, rows, pitch) {
  if (!prevIds || prevIds.length === 0) return rows.map(() => 0);
  return rows.map((row, newIndex) => {
    const oldIndex = prevIds.indexOf(row.unique_id);
    return oldIndex === -1 ? 0 : (oldIndex - newIndex) * pitch;
  });
}

/**
 * One element of `list` at random, or null when there is nothing to pick.
 *
 * The RNG is injected so a test can seed it, and it is consumed exactly once.
 * The index is clamped because a misbehaving `rnd()` -- 1.0, or negative --
 * must never index past the ends of the array.
 */
export function pickRandom(list, rnd = Math.random) {
  if (!list || list.length === 0) return null;
  const index = Math.floor(rnd() * list.length);
  return list[Math.min(list.length - 1, Math.max(0, index))];
}

export { LETTERS };
