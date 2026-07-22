import type { ContentBlock } from "./content-format";
import type { Project, Version } from "./data";

export type SearchResult = {
  id: string;
  targetId: string;
  chapter: string;
  knowledgePoint: string;
  context: string;
  kind: "资料章节" | "知识点" | "内部标题" | "正文" | "列表项";
};

const leadingTitleNumber = /^(?:\s*(?:第\s*[一二三四五六七八九十百\d]+\s*[章节篇]|[一二三四五六七八九十百\d]+[、.．)）]|[（(][一二三四五六七八九十百\d]+[）)]))+\s*/u;

function normalizeSearchTitle(value: string): string {
  return value
    .trim()
    .replace(leadingTitleNumber, "")
    .replace(/[^\w\u3400-\u9fff]+/g, "")
    .toLocaleLowerCase("zh-CN");
}

function sameDisplayTitle(left: string, right: string): boolean {
  const normalizedLeft = normalizeSearchTitle(left);
  const normalizedRight = normalizeSearchTitle(right);
  return Boolean(normalizedLeft && normalizedRight && normalizedLeft === normalizedRight);
}

function excerpt(text: string, query: string): string {
  const normalized = text.toLocaleLowerCase("zh-CN");
  const index = normalized.indexOf(query.toLocaleLowerCase("zh-CN"));
  const start = Math.max(0, index - 28);
  const end = Math.min(text.length, index + query.length + 42);
  return `${start > 0 ? "…" : ""}${text.slice(start, end)}${end < text.length ? "…" : ""}`;
}

export function searchProject(
  project: Project,
  version: Version,
  rawQuery: string,
  classifyContentBlocks: (content: string[]) => ContentBlock[],
): SearchResult[] {
  const query = rawQuery.trim();
  if (!query) return [];
  const matches: SearchResult[] = [];
  const add = (result: Omit<SearchResult, "context">, text: string) => {
    if (text.toLocaleLowerCase("zh-CN").includes(query.toLocaleLowerCase("zh-CN"))) {
      matches.push({ ...result, context: excerpt(text, query) });
    }
  };
  for (const chapter of project.chapters) {
    if (chapter.title) {
      add({ id: `chapter:${chapter.id}`, targetId: `${chapter.id}-title`, chapter: chapter.title, knowledgePoint: "", kind: "资料章节" }, chapter.title);
    }
    const chapterTitle = chapter.title ?? "";
    for (const knowledge of chapter.points) {
      add({ id: `knowledge:${knowledge.id}`, targetId: `${knowledge.id}-title`, chapter: chapterTitle, knowledgePoint: knowledge.title, kind: "知识点" }, knowledge.title);
      for (const point of knowledge.bulletPoints) {
        const versionPoint = point.versions[version];
        if (!sameDisplayTitle(knowledge.title, versionPoint.title)) {
          add({ id: `title:${point.id}`, targetId: `${point.id}-title`, chapter: chapterTitle, knowledgePoint: knowledge.title, kind: "内部标题" }, versionPoint.title);
        }
        if (version === "keywords") {
          versionPoint.content.forEach((text, index) => add({
            id: `body:${point.id}:${version}:${index}`,
            targetId: `${point.id}-${version}-item-${index}`,
            chapter: chapterTitle,
            knowledgePoint: knowledge.title,
            kind: "正文",
          }, text));
          continue;
        }
        classifyContentBlocks(versionPoint.content).forEach((block, blockIndex) => {
          if (block.kind === "paragraph") {
            const text = block.items.join("\n");
            add({
              id: `body:${point.id}:${version}:${blockIndex}`,
              targetId: `${point.id}-${version}-block-${blockIndex}`,
              chapter: chapterTitle,
              knowledgePoint: knowledge.title,
              kind: "正文",
            }, text);
          } else {
            block.items.forEach((text, itemIndex) => add({
              id: `list:${point.id}:${version}:${blockIndex}:${itemIndex}`,
              targetId: `${point.id}-${version}-block-${blockIndex}-item-${itemIndex}`,
              chapter: chapterTitle,
              knowledgePoint: knowledge.title,
              kind: "列表项",
            }, text));
          }
        });
      }
    }
  }
  return matches;
}
