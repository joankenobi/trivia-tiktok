// Unit test for the host room-fill plan.
//
// Run: node web/js/room_fill_test.mjs
//
// The plan is pure and lives in common.js for the same reason `currentRound`
// and `firstCorrectResponder` do: control.js runs side effects on load and
// cannot be imported here. Three outcomes must be expressible, and the rule that
// decides between them is the whole point of the button -- placement always
// wins, and a live from a previous UTC day is placed *with* a warning rather
// than refused, because the press is the host asserting they are streaming.

import { roomFillPlan } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

// The payload as `GET /lives/current` serves it.
const live = (over = {}) => ({
  room_id: "7691759076723772182",
  host_username: "@alexrmr74",
  started_at: "2026-10-01T23:12:00.000Z",
  stale: false,
  ...over,
});

// A live from today's UTC day: placed, nothing to warn about.
const fresh = roomFillPlan(live());
check("today's live is placed", fresh.place, true);
check("today's live places the room id", fresh.value, "7691759076723772182");
check("today's live carries no warning", fresh.stale, false);

// THE CASE THE BUTTON EXISTS FOR. 20:00 local (UTC-3) is 23:00 UTC, so after
// 21:00 local the auto-fill reads a still-running stream as "yesterday" and
// gives up. Placement wins over the warning: refusing here is what made the
// field look broken during the second half of a late show.
const stale = roomFillPlan(live({ stale: true }));
check("yesterday's live is placed anyway", stale.place, true);
check("yesterday's live places the room id", stale.value, "7691759076723772182");
check("yesterday's live carries the warning", stale.stale, true);

// Nothing to place: the route collapses "no live", "ended live" and "upstream
// answered with garbage" into this one shape. Refusal must carry no value and
// no warning, so the console cannot place a room id it never received.
check("null payload refuses", roomFillPlan(null).place, false);
check("refused plan carries no value", roomFillPlan(null).value, null);
check("refused plan never warns", roomFillPlan(null).stale, false);
check("missing payload refuses", roomFillPlan(undefined).place, false);
check("null room id refuses", roomFillPlan(live({ room_id: null })).place, false);
check("blank room id refuses", roomFillPlan(live({ room_id: "   " })).place, false);
check("an ended live is already a null payload upstream", roomFillPlan(live({ stale: false })).place, true);

// The plan decides, it does not remember: two calls must not share an object,
// or a second click could read a value the first one placed.
check("each call returns a fresh plan", roomFillPlan(live()) !== roomFillPlan(live()), true);

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);