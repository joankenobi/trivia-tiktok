// Tests for the waiting-cue helpers.
//
// Run: node web/js/waiting_test.mjs
//
// The question timer starts on the first answer now, so a round with no first
// answer has `deadline_epoch_ms === null` and must be treated as "waiting". The
// overlay replays a cue every `2 x question_time_limit_s`; a bad limit must
// never schedule a 0ms interval, which would spin the render loop.

import { isWaitingForFirstAnswer, waitingCueIntervalMs } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// --- isWaitingForFirstAnswer -------------------------------------------------

check("open round with no deadline is waiting", isWaitingForFirstAnswer({ status: "open", deadline_epoch_ms: null }), true);
check("open round with a deadline is not waiting", isWaitingForFirstAnswer({ status: "open", deadline_epoch_ms: 123 }), false);
check("pending round is not waiting", isWaitingForFirstAnswer({ status: "pending", deadline_epoch_ms: null }), false);
check("revealed round is not waiting", isWaitingForFirstAnswer({ status: "revealed", deadline_epoch_ms: null }), false);
check("missing deadline field counts as waiting", isWaitingForFirstAnswer({ status: "open" }), true);
check("zero deadline counts as waiting", isWaitingForFirstAnswer({ status: "open", deadline_epoch_ms: 0 }), true);
check("null round is not waiting", isWaitingForFirstAnswer(null), false);
check("undefined round is not waiting", isWaitingForFirstAnswer(undefined), false);

// --- waitingCueIntervalMs ----------------------------------------------------

check("20s limit cues every 40s", waitingCueIntervalMs(20), 40000);
check("number string is accepted", waitingCueIntervalMs("10"), 20000);
check("non-finite input never schedules a loop", waitingCueIntervalMs("nope"), 0);
check("missing input never schedules a loop", waitingCueIntervalMs(undefined), 0);
check("null input never schedules a loop", waitingCueIntervalMs(null), 0);
check("zero limit never schedules a loop", waitingCueIntervalMs(0), 0);
check("negative limit never schedules a loop", waitingCueIntervalMs(-5), 0);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
