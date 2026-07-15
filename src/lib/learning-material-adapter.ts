import { PointVersions, Project, Version } from "@/components/learning/data";
import { BackendContentVersion, BackendProject, LearningMaterialResponse } from "./revia-api";

function splitContent(version: Version, content: string): string[] {
  const separator = version === "keywords" ? /[\n、，,；;]+/ : /\n\s*\n+/;
  const parts = content.split(separator).map((part) => part.trim()).filter(Boolean);
  return parts.length > 0 ? parts : [content];
}

function versionMap(versions: BackendContentVersion[]): PointVersions {
  const find = (kind: Version) => versions.find((version) => version.kind === kind);
  const convert = (kind: Version) => {
    const value = find(kind);
    return { title: value?.title ?? "", content: splitContent(kind, value?.content ?? "") };
  };
  return { original: convert("original"), recitation: convert("recitation"), keywords: convert("keywords") };
}

export function toLearningProject(project: BackendProject, material: LearningMaterialResponse): Project {
  return {
    id: project.id,
    name: project.name,
    meta: "学习材料 · 已保存",
    documentTitle: `${project.name}复习材料`,
    chapters: material.chapters.map((chapter, chapterIndex) => ({
      id: chapter.id,
      number: String(chapter.position + 1 || chapterIndex + 1).padStart(2, "0"),
      title: chapter.title,
      points: chapter.knowledge_points.flatMap((knowledgePoint) =>
        knowledgePoint.bullet_points.map((bullet) => ({ id: bullet.id, versions: versionMap(bullet.versions) })),
      ),
    })),
  };
}

export function toProjectShell(project: BackendProject): Project {
  return { id: project.id, name: project.name, meta: "学习材料", documentTitle: `${project.name}复习材料`, chapters: [] };
}
