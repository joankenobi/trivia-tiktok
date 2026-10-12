// Unit test for the first-correct-responder selector.
//
// Run: node web/js/first_correct_test.mjs
//
// The bug this guards: the reveal frame showed the avatar badge but not the
// score, and a toast whose entrance animation replays on every 1s poll strobes
// on a live stream. So both the selection and the identity that keys the
// animation live here, pure, where plain node can check them without a DOM.

import { firstCorrectResponder, firstCorrectKey } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// A first responder as the API serves it: the reveal frame needs both numbers.
const first = (unique_id) => ({
  unique_id,
  nickname: unique_id,
  avatar_path: `/avatars/${unique_id}.webp`,
  event_id: 1001,
  ts_epoch_us: 1,
  points: 40,
  total_points: 120,
});

const round = (over = {}) => ({
  id: 7,
  status: "revealed",
  correct_option: "A",
  tally: {
    counts: { A: 2, B: 1, C: 0, D: 0 },
    unique_voters: 3,
    first_by_option: { A: first("alice"), B: first("bob") },
  },
  ...over,
});

// The happy path: the viewer who answered the CORRECT option first.
check(
  "revealed round announces the first correct responder",
  firstCorrectResponder(round()).unique_id,
  "alice",
);

// The whole point of the feature: the announcement carries both numbers, so the
// overlay never has to recompute anything.
check("winner carries the award for this answer", firstCorrectResponder(round()).points, 40);
check("winner carries the show total", firstCorrectResponder(round()).total_points, 120);

// No toast before the answer is on screen. `closed` already stores
// correct_option in the DB but the API only exposes it once revealed.
check("open round has nobody to announce", firstCorrectResponder(round({ status: "open" })), null);
check("closed but unrevealed round stays silent", firstCorrectResponder(round({ status: "closed" })), null);
check("pending round stays silent", firstCorrectResponder(round({ status: "pending" })), null);

// Revealed, but nobody picked the right letter: nothing to announce.
check(
  "nobody voted the correct option",
  firstCorrectResponder(round({ tally: { first_by_option: { B: first("bob") }, counts: {}, unique_voters: 1 } })),
  null,
);

// Defensive shapes: a payload must never crash the overlay's render loop.
check("no round", firstCorrectResponder(undefined), null);
check("revealed without a frozen tally", firstCorrectResponder(round({ tally: null })), null);
check("revealed without an answer exposed", firstCorrectResponder(round({ correct_option: null })), null);

// THE FLICKER GUARD. `render()` rebuilds its DOM every poll, so the entrance
// animation may only run when this key changes. Same key on two consecutive
// polls of the same revealed round is what keeps it from strobing.
const revealed = round();
check(
  "same round polled twice keeps the same key",
  firstCorrectKey(revealed, firstCorrectResponder(revealed)) ===
    firstCorrectKey(revealed, firstCorrectResponder(revealed)),
  true,
);
check(
  "a new round re-runs the animation even for the same viewer",
  firstCorrectKey(round({ id: 8 }), firstCorrectResponder(round({ id: 8 }))) !==
    firstCorrectKey(revealed, firstCorrectResponder(revealed)),
  true,
);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
