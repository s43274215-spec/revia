import { Fragment, MouseEvent, ReactNode } from "react";
import { BulletPoint, Project, Version, versionLabels } from "./data";
import { classifyContentBlocks } from "./content-format";
import { generationFailureCounts, successfulGenerationCount } from "@/lib/generation-failures";
import type { GenerationJob } from "@/lib/revia-api";

type ReadingContentProps = { project: Project; version: Version; query: string; onKeyword: (point: BulletPoint) => void; onPointContext: (event: MouseEvent, point: BulletPoint) => void };

function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return <>{parts.map((part, index) => part.toLocaleLowerCase("zh-CN") === query.toLocaleLowerCase("zh-CN") ? <mark key={index}>{part}</mark> : part)}</>;
}

function Lines({ lines, query }: { lines: string[]; query: string }) {
  return <>{lines.map((line, index) => <Fragment key={index}>{index > 0 && <br />}<Highlighted text={line} query={query} /></Fragment>)}</>;
}

export function renderContentBlocks(content: string[], query: string, idPrefix = "content", activeTargetId?: string): ReactNode[] {
  return classifyContentBlocks(content).map((block, blockIndex) => {
    if (block.kind === "ordered") {
      return <ol className="content-list" key={blockIndex}>{block.items.map((item, index) => { const id = `${idPrefix}-block-${blockIndex}-item-${index}`; return <li className={activeTargetId === id ? "is-search-target" : ""} id={id} key={index}><Highlighted text={item} query={query} /></li>; })}</ol>;
    }
    if (block.kind === "unordered") {
      return <ul className="content-list" key={blockIndex}>{block.items.map((item, index) => { const id = `${idPrefix}-block-${blockIndex}-item-${index}`; return <li className={activeTargetId === id ? "is-search-target" : ""} id={id} key={index}><Highlighted text={item} query={query} /></li>; })}</ul>;
    }
    const id = `${idPrefix}-block-${blockIndex}`;
    return <p className={activeTargetId === id ? "is-search-target" : ""} id={id} key={blockIndex}><Lines lines={block.items} query={query} /></p>;
  });
}

export function ReadingContent({ project, version, query, activeTargetId, onKeyword, onPointContext, partialJob }: ReadingContentProps & { activeTargetId?: string; partialJob: GenerationJob | null }) {
  const failureCounts = partialJob ? generationFailureCounts(partialJob.item_failures) : null;
  return (
    <article className="reading-document" data-project={project.id}>
      <div className="document-kicker">{project.name} · 复习材料</div>
      <h1>{version === "keywords" ? "核心概念与记忆线索" : project.documentTitle}</h1>
      <p className="document-intro">{version === "keywords" ? "点击任一关键词，查看该要点对应的背诵内容。" : `当前为${versionLabels[version]}，按课程章节连续阅读全部内容。`}</p>
      {partialJob && failureCounts && <aside className="partial-generation-summary" aria-label="部分生成结果">
        <span>部分内容已生成</span>
        <strong>{successfulGenerationCount(partialJob)} / {partialJob.total_items} 个考点可阅读</strong>
        <p>{failureCounts.unmatched > 0 && `${failureCounts.unmatched} 个未找到资料依据`}{failureCounts.unmatched > 0 && failureCounts.schema_validation > 0 && " · "}{failureCounts.schema_validation > 0 && `${failureCounts.schema_validation} 个未通过格式检查`}{failureCounts.generation_error > 0 && ` · ${failureCounts.generation_error} 个生成未完成`}。详情已保留在左侧目录。</p>
      </aside>}
      {project.chapters.map((chapter) => (
        <section className="chapter-section" id={chapter.id} key={chapter.id}>
          {chapter.title && <>
            <div className="chapter-number">第 {chapter.number} 章</div>
            <h2 id={`${chapter.id}-title`} className={activeTargetId === `${chapter.id}-title` ? "is-search-target" : ""}>{chapter.title}</h2>
          </>}
          {chapter.points.map((knowledgePoint) => (
            <section className="knowledge-section" id={knowledgePoint.id} key={knowledgePoint.id}>
              <h3 id={`${knowledgePoint.id}-title`} className={activeTargetId === `${knowledgePoint.id}-title` ? "is-search-target" : ""}>{knowledgePoint.title}</h3>
              <ol className={`bullet-point-list ${knowledgePoint.bulletPoints.length === 1 ? "is-single" : ""}`}>
                {knowledgePoint.bulletPoints.map((point) => (
                  <li className="bullet-point" id={point.id} data-point-id={point.id} key={point.id} onContextMenu={(event) => onPointContext(event, point)}>
                    <h4 id={`${point.id}-title`} className={activeTargetId === `${point.id}-title` ? "is-search-target" : ""}>{point.versions[version].title}</h4>
                    {version === "keywords" ? (
                      <div className="keyword-lines">
                        {point.versions.keywords.content.map((keyword, index) => (
                          <button id={`${point.id}-${version}-item-${index}`} className={activeTargetId === `${point.id}-${version}-item-${index}` ? "is-search-target" : ""} key={`${point.id}-${index}`} onClick={() => onKeyword(point)}>
                            <span>{String(index + 1).padStart(2, "0")}</span><strong><Highlighted text={keyword} query={query} /></strong><em>查看背诵内容</em>
                          </button>
                        ))}
                      </div>
                    ) : <div className="bullet-content">{renderContentBlocks(point.versions[version].content, query, `${point.id}-${version}`, activeTargetId)}</div>}
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </section>
      ))}
      <footer className="document-end"><span />本章内容结束<span /></footer>
    </article>
  );
}
