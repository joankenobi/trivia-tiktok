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

export { LETTERS };
