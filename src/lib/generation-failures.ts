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
  const longBulletTitle = failure.reason.match(/bullet_points\.(\d+)\.(?:title|(?:original|recitation|keywords)\.title):[^;]*(?:at most 25|not exceed 25)/i);
  if (longBulletTitle) {
    return `第 ${Number(longBulletTitle[1]) + 1} 个小标题超过旧的 25 字排版建议；修复后不会再因此丢弃有效内容。`;
  }
  if (generationFailureKind(failure) === "schema_validation") {
    const details = failure.reason.match(/three-version item schema:\s*(.+)$/i)?.[1]?.trim();
    if (details) {
      return `生成内容在自动修复后仍未通过结构检查：${details}`;
    }
    return "生成内容在自动修复后仍未通过结构检查，后台未保存可安全使用的结果。";
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
