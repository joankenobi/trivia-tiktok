// Regression test for `currentRound()`.
//
// Run: node web/js/current_round_test.mjs
//
// The bug this guards: the overlay resolved the visible round with a forward
// `find(status !== "pending")`, which returns position 0. While round 2 was
// voting an `open` round won and hid it; the moment round 2 closed, the overlay
// fell back to position 0 and showed question 1 together with its answer.

import { currentRound } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

const r = (position, status) => ({ position, status });

// Nothing has played: the console shows the first round as an up-next preview.
check("all pending falls back to nothing", currentRound([r(0, "pending"), r(1, "pending")]), undefined);

// Round 1 voting.
check("round 1 open", currentRound([r(0, "open"), r(1, "pending")]).position, 0);

// Round 1 revealed, nothing else played yet.
check("round 1 revealed", currentRound([r(0, "revealed"), r(1, "pending")]).position, 0);

// Round 2 voting. The open round must win over the earlier revealed round.
check("round 2 open beats round 1 revealed", currentRound([r(0, "revealed"), r(1, "open")]).position, 1);

// THE BUG. Round 2 just closed and revealed. No open round exists, so the
// fallback decides what the audience sees. A forward scan returns round 0 here
// and shows question 1 with its answer.
check(
  "round 2 revealed must NOT fall back to round 1",
  currentRound([r(0, "revealed"), r(1, "revealed")]).position,
  1,
);
check(
  "round 2 revealed keeps its own status",
  currentRound([r(0, "revealed"), r(1, "revealed")]).status,
  "revealed",
);

// Same, with three rounds and the last one closing.
check(
  "last of three revealed wins",
  currentRound([r(0, "revealed"), r(1, "revealed"), r(2, "revealed")]).position,
  2,
);

// A host-closed round (never revealed) is still the round on screen.
check(
  "round 3 closed by hand",
  currentRound([r(0, "revealed"), r(1, "revealed"), r(2, "closed")]).position,
  2,
);

// Unsorted input must not change the answer.
check(
  "position order wins over array order",
  currentRound([r(2, "revealed"), r(0, "revealed"), r(1, "revealed")]).position,
  2,
);
check(
  "unsorted open round still wins",
  currentRound([r(2, "pending"), r(1, "open"), r(0, "revealed")]).position,
  1,
);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);