// Unit test for the random push-sound picker.
//
// Run: node web/js/sounds_test.mjs
//
// The overlay plays one sound at random per animation run. The picker is pure
// and takes an injected RNG so a seeded call makes the choice deterministic;
// the index is clamped because an injected RNG that returns exactly 1.0 (or a
// negative value) must never read past the end of the list.

import { pickRandom } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// Nothing to play: the caller must get null, not a crash and not undefined.
check("empty list yields null", pickRandom([], () => 0.5), null);
check("null list yields null", pickRandom(null, () => 0.5), null);

// One sound: it is the only choice, whatever the RNG rolled.
check("single element is returned", pickRandom(["only"], () => 0.0), "only");
check("single element survives a roll at the edge", pickRandom(["only"], () => 0.99), "only");

// THE CLAMP. A seeded RNG at 0.99 on three items must land on the last one
// instead of reading index 3, which does not exist.
check("a roll near 1.0 picks the last element", pickRandom(["a", "b", "c"], () => 0.99), "c");

// A mid roll on a two-element list: floor(0.5 * 2) === 1.
check("a roll of 0.5 on two picks the second", pickRandom(["a", "b"], () => 0.5), "b");

// Defensive: an RNG that misbehaves below zero must still return a real element.
check("a negative roll is clamped to the first", pickRandom(["a", "b", "c"], () => -1), "a");

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
