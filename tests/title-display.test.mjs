import assert from "node:assert/strict";
import test from "node:test";

import { normalizeDisplayTitle, sameDisplayTitle } from "../src/components/learning/data.ts";

test("treats numbering, punctuation and case as display-only title differences", () => {
  assert.equal(normalizeDisplayTitle("第 1 节：绩效管理"), "绩效管理");
  assert.equal(sameDisplayTitle("绩效管理", "第 1 节：绩效管理"), true);
  assert.equal(sameDisplayTitle("ABC 模型", "abc模型"), true);
});

test("does not hide a more specific child title", () => {
  assert.equal(sameDisplayTitle("绩效管理", "绩效管理的基本流程"), false);
  assert.equal(sameDisplayTitle("绩效管理", "绩效评价"), false);
});
