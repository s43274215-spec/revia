import type { GenerationItemFailure, GenerationJob } from "./revia-api";

export type GenerationFailureKind = "unmatched" | "schema_validation" | "generation_error";

export function generationFailureKind(failure: GenerationItemFailure): GenerationFailureKind {
  if (failure.failure_type === "unmatched" || failure.reason.toLowerCase().includes("unmatched")) {
    return "unmatched";
  }
  if (failure.failure_type === "schema_validation" || failure.reason.toLowerCase().includes("schema")) {
    return "schema_validation";
  }
  return "generation_error";
}

export function generationFailureLabel(failure: GenerationItemFailure): string {
  const kind = generationFailureKind(failure);
  if (kind === "unmatched") return "资料依据不足";
  if (kind === "schema_validation") return "生成格式未通过检查";
  return "生成未完成";
}

export function generationFailureReason(failure: GenerationItemFailure): string {
  const reason = failure.reason.toLowerCase();
  if (generationFailureKind(failure) === "unmatched") {
    return "课程资料中没有找到达到相关性要求的内容。";
  }
  if (reason.includes("keywords content must contain between 3 and 8")) {
    return "生成的关键词数量不符合 3–8 个的格式要求。";
  }
  if (reason.includes("at most 800 characters")) {
    return "生成的原文内容超过 800 字的长度限制。";
  }
  if (generationFailureKind(failure) === "schema_validation") {
    return "生成内容在自动修复后仍未通过格式检查。";
  }
  return "生成服务未能完成这个考点。";
}

export function generationFailureCounts(failures: GenerationItemFailure[]) {
  return failures.reduce((counts, failure) => {
    counts[generationFailureKind(failure)] += 1;
    return counts;
  }, { unmatched: 0, schema_validation: 0, generation_error: 0 });
}

export function successfulGenerationCount(job: GenerationJob): number {
  return job.successful_items ?? Math.max(0, job.total_items - job.item_failures.length);
}
