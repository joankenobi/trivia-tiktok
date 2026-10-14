// Overlay sounds.
//
// The overlay plays one short sound at random for push animations, and a
// waiting cue while a round waits for its first answer. Each folder has its
// own endpoint, cached list, preloaded Audio map and a shared promise so early
// callers do not race. Every failure is swallowed: missing sounds must never
// break the render loop that keeps the stream alive.

import { api, pickRandom } from "./common.js";

// `api()` already prepends "/api/v1" (see common.js), so these must be the
// paths WITHOUT that prefix. Passing the full "/api/v1/..." path here composed
// "/api/v1/api/v1/..." and 404'd into the swallow-everything catch below.
const PUSH_ENDPOINT = "/sounds/pushPlayer";
const WAITING_ENDPOINT = "/sounds/waiting";
const CORRECT_ENDPOINT = "/sounds/correct";

// Exported so a test can assert the paths stay prefix-free (the regression that
// silenced every cue is invisible at runtime).
export const SOUND_ENDPOINTS = {
  push: PUSH_ENDPOINT,
  waiting: WAITING_ENDPOINT,
  correct: CORRECT_ENDPOINT,
};

function createSoundPlayer(endpoint) {
  let loadPromise = null;
  let soundUrls = [];
  const players = new Map();
  // The play() rejection repeats for every cue while the stream runs, so the
  // warning is collapsed to one line per player instead of flooding the console.
  let warnedPlayback = false;

  async function load() {
    try {
      const data = await api(endpoint);
      soundUrls = Array.isArray(data?.sounds) ? data.sounds : [];
    } catch (error) {
      // Still non-fatal: silence must never break the render loop. But silence
      // with no trace is exactly how a wrong prefix muted the whole overlay, so
      // the failure is reported once (the promise caches this call per player).
      console.warn(`[sounds] could not list ${endpoint}:`, error);
      soundUrls = [];
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
    if (!loadPromise) loadPromise = load();
    return loadPromise;
  }

  function preload() {
    ensureLoaded();
  }

  async function playRandom() {
    const urls = await ensureLoaded();
    const url = pickRandom(urls);
    if (!url) return;
    const audio = players.get(url);
    if (!audio) return;
    try {
      audio.currentTime = 0;
    } catch {
      /* benign: the clip is simply not seekable until it has loaded */
    }
    try {
      const played = audio.play();
      if (played && typeof played.catch === "function") {
        played.catch((error) => {
          if (warnedPlayback) return;
          warnedPlayback = true;
          console.warn(`[sounds] playback blocked for ${url}:`, error);
        });
      }
    } catch (error) {
      if (!warnedPlayback) {
        warnedPlayback = true;
        console.warn(`[sounds] playback failed for ${url}:`, error);
      }
    }
  }

  return { preload, playRandom };
}

const pushPlayer = createSoundPlayer(PUSH_ENDPOINT);
const waitingPlayer = createSoundPlayer(WAITING_ENDPOINT);
const correctPlayer = createSoundPlayer(CORRECT_ENDPOINT);

export function preloadPushSounds() {
  pushPlayer.preload();
}

export async function playRandomPushSound() {
  return pushPlayer.playRandom();
}

export function preloadWaitingSounds() {
  waitingPlayer.preload();
}

export async function playRandomWaitingSound() {
  return waitingPlayer.playRandom();
}

export function preloadCorrectSounds() {
  correctPlayer.preload();
}

export async function playRandomCorrectSound() {
  return correctPlayer.playRandom();
}
