// Unit test for the reveal-cue sequencer.
//
// Run: node web/js/cue_sequencer_test.mjs
//
// The overlay plays its reveal cues one after another: the toast's sound, then
// the correct-answer sound, then the ranking swap's sound. One FIFO sequencer is
// what guarantees no cue starts while the previous one is still playing, so it
// must (a) run tasks strictly in order, (b) report `busy` for the whole
// enqueued-or-running span, (c) survive a task that throws without wedging the
// chain, and (d) settle each caller's promise only after its own task finished.
// Dependency-free and DOM-free, so plain node can check it.

import { createCueSequencer } from "./common.js";

let failures = 0;

function check(name, actual, expected) {
  const ok = actual === expected;
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `\n        got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`}`);
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  // --- strict serial order: no task starts before the previous finished ---
  {
    const cues = createCueSequencer();
    const log = [];
    const first = cues.run(async () => {
      log.push("a:start");
      await delay(20);
      log.push("a:end");
    });
    const second = cues.run(async () => {
      log.push("b:start");
      await delay(20);
      log.push("b:end");
    });
    await Promise.all([first, second]);
    check("tasks never overlap", log.join(","), "a:start,a:end,b:start,b:end");
  }

  // --- busy is true while queued or running, false once drained ---
  {
    const cues = createCueSequencer();
    check("idle sequencer is not busy", cues.busy, false);
    const gate = cues.run(async () => {
      await delay(20);
    });
    check("busy while the first task runs", cues.busy, true);
    const queued = cues.run(async () => {
      await delay(5);
    });
    check("busy while a second task is queued", cues.busy, true);
    await gate;
    check("still busy while the queued task runs", cues.busy, true);
    await queued;
    check("not busy once drained", cues.busy, false);
  }

  // --- a rejecting task must not wedge the chain ---
  {
    const cues = createCueSequencer();
    const log = [];
    const bad = cues.run(async () => {
      log.push("bad");
      throw new Error("boom");
    });
    bad.catch(() => {});
    const good = cues.run(async () => {
      log.push("good");
    });
    await good;
    check("the task after a reject still runs", log.join(","), "bad,good");
    check("the sequencer drains after a reject", cues.busy, false);
  }

  // --- run() settles only after its own task completed ---
  {
    const cues = createCueSequencer();
    let finished = false;
    const p = cues.run(async () => {
      await delay(20);
      finished = true;
    });
    check("the task has not finished before its promise settles", finished, false);
    await p;
    check("the task finished once its promise settled", finished, true);
  }

  console.log(failures === 0 ? "\nall passed" : `\n${failures} failed`);
  process.exit(failures === 0 ? 0 : 1);
}

main();
