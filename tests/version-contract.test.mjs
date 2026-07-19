import assert from "node:assert/strict";
import test from "node:test";

import { versionContract, versionLabels } from "../src/components/learning/data.ts";

test("content versions keep the confirmed names and order", () => {
  assert.deepEqual(versionContract, [
    { id: "original", label: "原文版本" },
    { id: "recitation", label: "背诵版本" },
    { id: "keywords", label: "关键词版本" },
  ]);
  assert.deepEqual(versionLabels, {
    original: "原文版本",
    recitation: "背诵版本",
    keywords: "关键词版本",
  });
});
