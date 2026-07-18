import assert from "node:assert/strict";
import test from "node:test";

import { isTransientNetworkError, SinglePromiseGate } from "../src/lib/generation-reliability.ts";

test("one pending generation promise suppresses duplicate starts", async () => {
  const gate = new SinglePromiseGate();
  let starts = 0;
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const factory = () => {
    starts += 1;
    return pending;
  };

  const first = gate.run(factory);
  const second = gate.run(factory);

  assert.equal(first, second);
  assert.equal(starts, 1);
  assert.equal(gate.pending, true);
  finish({ id: "existing-job" });
  await first;
  assert.equal(gate.pending, false);
});

test("Failed to fetch selects reconnection instead of a second generation start", async () => {
  const gate = new SinglePromiseGate();
  let starts = 0;
  let lookups = 0;

  try {
    await gate.run(async () => {
      starts += 1;
      throw new TypeError("Failed to fetch");
    });
  } catch (error) {
    assert.equal(isTransientNetworkError(error), true);
    lookups += 1;
  }

  assert.equal(starts, 1);
  assert.equal(lookups, 1);
});

test("ordinary server failures remain explicit failures", () => {
  assert.equal(isTransientNetworkError(new Error("请求失败（HTTP 503）")), false);
});
