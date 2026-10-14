// Overlay push sounds.
//
// The overlay plays one short sound at random every time an animation runs (the
// first-correct toast and the ranking swap). The list is fetched once, each URL
// gets its own preloaded `Audio`, and every failure is swallowed: a missing
// sound must never break the render loop that keeps the stream alive.

import { api, pickRandom } from "/js/common.js";

const ENDPOINT = "/api/v1/sounds/pushPlayer";

// The promise is cached, not just the resolved list, so two early callers share
// one request instead of racing. The array and the players are cached too, so a
// later play does no work at all.
let loadPromise = null;
let soundUrls = [];
const players = new Map();

async function loadPushSounds() {
  try {
    const data = await api(ENDPOINT);
    soundUrls = Array.isArray(data?.sounds) ? data.sounds : [];
  } catch {
    soundUrls = []; // unreachable API is silent, not fatal
  }
  for (const url of soundUrls) {
    if (players.has(url)) continue;
    const audio = new Audio(url);
    audio.preload = "auto";
    players.set(url, audio);
  }
  return soundUrls;
}

function ensureLoaded() {
  if (!loadPromise) loadPromise = loadPushSounds();
  return loadPromise;
}

// Fire-and-forget warm-up for an early call at boot; the result is not awaited.
export function preloadPushSounds() {
  ensureLoaded();
}

export async function playRandomPushSound() {
  const urls = await ensureLoaded();
  const url = pickRandom(urls);
  if (!url) return;
  const audio = players.get(url);
  if (!audio) return;
  try {
    // Resetting is what allows the same clip to fire twice in a row; it can
    // throw before the metadata is known, which is not a reason to skip play().
    audio.currentTime = 0;
  } catch {
    /* ignore */
  }
  try {
    // Autoplay policy rejects the returned promise on an unclicked document;
    // the cue is best-effort, so the rejection is swallowed.
    const played = audio.play();
    if (played && typeof played.catch === "function") played.catch(() => {});
  } catch {
    /* ignore */
  }
}
