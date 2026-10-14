// Regression test for `pollView()`.
//
// Run: node web/js/poll_view_test.mjs
//
// The bug this guards: the overlay poll drew the active round on every tick,
// including the ticks that happen after the show is over. `renderFinal()` keeps
// resetting the toast and rank reveal guards (it has to, so the next show
// announces cleanly), so re-drawing the last round re-ran its reveal once per
// second -- replaying the push cue and the rank swap over the finished podium.
// A finished show must never resolve to the "round" view.

import { pollView } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

const r = (position, status) => ({ position, status });
const show = (status, rounds) => ({ status, rounds });

// No payload yet (first tick, or the discovery request has not landed).
check("missing show draws nothing", pollView(null).mode, "idle");
check("missing show carries no round", pollView(null).round, null);

// THE BUG. The show just finished with its last round revealed, which is the
// shape the poll sees on every tick for the whole final board.
check("finished show draws the podium", pollView(show("finished", [r(0, "revealed"), r(1, "revealed")])).mode, "final");
check("finished show never draws a round", pollView(show("finished", [r(0, "revealed"), r(1, "revealed")])).round, null);

// Status wins over the round list: even a payload with an open round is final.
check("finished wins over an open round", pollView(show("finished", [r(0, "open")])).mode, "final");

// A live show keeps drawing the round it is playing.
check("running show draws its revealed round", pollView(show("running", [r(0, "revealed"), r(1, "pending")])).mode, "round");
check("running show draws the open round", pollView(show("running", [r(0, "revealed"), r(1, "open")])).round.position, 1);

// Nothing played yet: the console shows the intro, not a round.
check("all-pending show draws nothing", pollView(show("running", [r(0, "pending"), r(1, "pending")])).mode, "idle");
check("all-pending show has no round", pollView(show("running", [r(0, "pending")])).round, null);
check("ready show draws nothing", pollView(show("ready", [])).mode, "idle");

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
