// Unit test for the correct-answer reveal cue delay.
//
// Run: node web/js/correct_cue_test.mjs
//
// When a round is revealed the overlay plays a random sound from
// web/sounds/correct, once per reveal. If a winner exists the winner jingle
// ("¡Primero en acertar!") lands first, so the correct cue is delayed by a gap
// to avoid stacking on it; with no winner it fires immediately. The delay is a
// pure function so plain node can check it without a DOM.

import { CORRECT_CUE_DELAY_MS, correctCueDelayMs } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// Nobody answered correctly: the answer still reveals, so the cue must fire
// with no delay at all.
check("no winner fires the cue immediately", correctCueDelayMs(false), 0);

// A winner exists: the cue waits out the jingle instead of overlapping it.
check("a winner delays the cue by the constant", correctCueDelayMs(true), CORRECT_CUE_DELAY_MS);

// The whole point of the constant: the gap is strictly positive, so the two
// cues can never start together.
check("the winner delay is strictly positive", correctCueDelayMs(true) > 0, true);

// Defensive: any truthy input is treated as "a winner exists".
check("a truthy non-boolean behaves as a winner", correctCueDelayMs("yes"), CORRECT_CUE_DELAY_MS);

// The constant itself is a real, positive number of milliseconds.
check("the constant is a positive number of ms", CORRECT_CUE_DELAY_MS > 0, true);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
