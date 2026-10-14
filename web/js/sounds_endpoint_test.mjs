// Regression guard for the sound endpoints.
//
// Run: node web/js/sounds_endpoint_test.mjs
//
// `api()` prepends "/api/v1". Handing it a full "/api/v1/..." path as well
// built "/api/v1/api/v1/...", which 404'd straight into the catch that swallows
// every sound failure: the overlay went completely silent with nothing in the
// console. The bug survived two features because nothing asserted the
// composition, and `new Audio()` cannot be exercised without a DOM. So the
// cheap, honest guard is to pin the rule: paths are prefix-free, and the
// composed URL is exactly what the server serves.

import { apiUrl } from "./common.js";
import { SOUND_ENDPOINTS } from "./sounds.js";

let failures = 0;

function check(name, ok) {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}

const entries = Object.entries(SOUND_ENDPOINTS);

check("all three sound folders are wired", entries.length === 3);

for (const [name, path] of entries) {
  check(`${name}: path is a non-empty string`, typeof path === "string" && path.length > 0);
  check(`${name}: path is prefix-free (does not start with /api)`, !path.startsWith("/api"));
  check(`${name}: path starts with /sounds/`, path.startsWith("/sounds/"));

  const url = apiUrl(path);
  check(`${name}: composed url equals /api/v1 + path`, url === "/api/v1" + path);
  check(`${name}: composed url has no doubled prefix`, !url.startsWith("/api/v1/api/"));
  check(`${name}: composed url hits the listing route`, url === `/api/v1${path}`);
}

console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
process.exit(failures === 0 ? 0 : 1);
