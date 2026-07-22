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
const longTitleSchema = {
  syllabus_item: "科学社会主义的基本原则",
  failure_type: "schema_validation",
  reason: "AI output failed schema validation after one structure-repair retry: AI output does not match the three-version item schema: bullet_points.3.title: String should have at most 25 characters; bullet_points.3.original.title: String should have at most 25 characters",
};
const unreadableSchema = {
  syllabus_item: "胜任素质模型",
  failure_type: "schema_validation",
  reason: "AI output failed schema validation and contained no readable content after salvage: AI output contained no readable learning content after deterministic salvage",
};

test("legacy generation failures are classified from their stored reasons", () => {
  assert.equal(generationFailureKind(unmatched), "unmatched");
  assert.equal(generationFailureKind(schema), "schema_validation");
  assert.deepEqual(generationFailureCounts([unmatched, schema]), {
    unmatched: 1,
    schema_validation: 1,
    generation_error: 0,
    format_warning: 0,
  });
});

test("internal validation errors become readable failure reasons", () => {
  assert.equal(generationFailureReason(unmatched), "课程资料中没有找到达到相关性要求的内容。");
  assert.equal(generationFailureReason(schema), "生成的原文内容超过 800 字的长度限制。");
  assert.equal(
    generationFailureReason(longTitleSchema),
    "第 4 个小标题超过旧的 25 字排版建议；修复后不会再因此丢弃有效内容。",
  );
  assert.equal(
    generationFailureReason(unreadableSchema),
    "AI 两次返回的结构均损坏，且未能提取到可读正文。",
  );
});

test("job success count prefers the durable backend count", () => {
  const job = { successful_items: 30, total_items: 48, item_failures: Array(18).fill(unmatched) };
  assert.equal(successfulGenerationCount(job), 30);
});
