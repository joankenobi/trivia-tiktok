// Unit test for the ranking swap offsets.
//
// Run: node web/js/rank_test.mjs
//
// The overlay animates the leaderboard by first placing every row at the spot
// it held in the previous poll and then letting a CSS transition carry it to
// its new one. This pure helper computes that starting spot: for each row of
// the NEW order, `(oldIndex - newIndex) * pitch`. A row with no old index (a
// newcomer) starts where it will end up, and an empty/null previous order means
// there is nothing to animate from.

import { rankOffsets } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// A RankingEntry as the API serves it: the arrays the overlay actually holds.
const rows = (ids) => ids.map((unique_id) => ({ unique_id, nickname: unique_id, points: 0 }));

const eq = (ids) => JSON.stringify(ids);

const PITCH = 60;

// Nothing moved: every row starts where it already is, so no transform runs.
check(
  "identical order has no offsets",
  eq(rankOffsets(["a", "b", "c", "d"], rows(["a", "b", "c", "d"]), PITCH)),
  eq([0, 0, 0, 0]),
);

// The swap the feature exists for: b and a traded places. `a` now leads and
// starts one row BELOW (positive offset); `b` dropped and starts one row ABOVE.
const swapped = rankOffsets(["b", "a", "c", "d"], rows(["a", "b", "c", "d"]), PITCH);
check("a swapped up from second starts a row lower", swapped[0], PITCH);
check("b swapped down from first starts a row higher", swapped[1], -PITCH);
check("untouched rows do not move", `${swapped[2]},${swapped[3]}`, "0,0");
check(
  "the whole swap as one array",
  eq(swapped),
  eq([PITCH, -PITCH, 0, 0]),
);

// A newcomer has no previous place to come from, so it must not fly in from a
// phantom position: it starts at its final spot.
const withNewcomer = rankOffsets(["a", "b"], rows(["a", "b", "carol"]), PITCH);
check("a newcomer starts at its final spot", withNewcomer[2], 0);

// No previous ranking at all (first reveal): everything is a newcomer, so there
// is nothing to animate and every offset is zero.
check(
  "null prevIds yields all zeros",
  eq(rankOffsets(null, rows(["a", "b", "c"]), PITCH)),
  eq([0, 0, 0]),
);
check(
  "empty prevIds yields all zeros",
  eq(rankOffsets([], rows(["a", "b", "c"]), PITCH)),
  eq([0, 0, 0]),
);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
