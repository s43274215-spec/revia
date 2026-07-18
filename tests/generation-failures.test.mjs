import assert from "node:assert/strict";
import test from "node:test";
import {
  generationFailureCounts,
  generationFailureKind,
  generationFailureReason,
  successfulGenerationCount,
} from "../src/lib/generation-failures.ts";

const unmatched = {
  syllabus_item: "X-Y 理论",
  reason: "unmatched: no TextChunk met the configured relevance threshold",
};
const schema = {
  syllabus_item: "人力资源规划",
  reason: "AI output does not match schema: String should have at most 800 characters",
};

test("legacy generation failures are classified from their stored reasons", () => {
  assert.equal(generationFailureKind(unmatched), "unmatched");
  assert.equal(generationFailureKind(schema), "schema_validation");
  assert.deepEqual(generationFailureCounts([unmatched, schema]), {
    unmatched: 1,
    schema_validation: 1,
    generation_error: 0,
  });
});

test("internal validation errors become readable failure reasons", () => {
  assert.equal(generationFailureReason(unmatched), "课程资料中没有找到达到相关性要求的内容。");
  assert.equal(generationFailureReason(schema), "生成的原文内容超过 800 字的长度限制。");
});

test("job success count prefers the durable backend count", () => {
  const job = { successful_items: 30, total_items: 48, item_failures: Array(18).fill(unmatched) };
  assert.equal(successfulGenerationCount(job), 30);
});
