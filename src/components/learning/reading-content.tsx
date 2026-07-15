import { MouseEvent } from "react";
import { KnowledgePoint, Project, Version, versionLabels } from "./data";

type ReadingContentProps = { project: Project; version: Version; query: string; onKeyword: (point: KnowledgePoint) => void; onPointContext: (event: MouseEvent, point: KnowledgePoint) => void };

function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return text;
  const index = text.toLowerCase().indexOf(query.toLowerCase());
  if (index < 0) return text;
  return <>{text.slice(0, index)}<mark>{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length)}</>;
}

export function ReadingContent({ project, version, query, onKeyword, onPointContext }: ReadingContentProps) {
  return (
    <article className="reading-document" data-project={project.id}>
      <div className="document-kicker">{project.name} · 复习材料</div>
      <h1>{version === "keywords" ? "核心概念与记忆线索" : project.documentTitle}</h1>
      <p className="document-intro">{version === "keywords" ? "点击任一关键词，查看该要点对应的背诵内容。" : `当前为${versionLabels[version]}，按课程章节连续阅读全部内容。`}</p>
      {project.chapters.map((chapter) => (
        <section className="chapter-section" id={chapter.id} key={chapter.id}>
          <div className="chapter-number">第 {chapter.number} 章</div>
          <h2>{chapter.title}</h2>
          {chapter.points.map((point) => (
            <section className="knowledge-section" id={point.id} data-point-id={point.id} key={point.id} onContextMenu={(event) => onPointContext(event, point)}>
              <h3>{point.versions[version].title}</h3>
              {version === "keywords" ? (
                <div className="keyword-lines">
                  {point.versions.keywords.content.map((keyword, index) => (
                    <button key={`${point.id}-${index}`} onClick={() => onKeyword(point)}>
                      <span>{String(index + 1).padStart(2, "0")}</span><strong><Highlighted text={keyword} query={query} /></strong><em>查看背诵内容</em>
                    </button>
                  ))}
                </div>
              ) : point.versions[version].content.map((paragraph, index) => <p key={`${point.id}-${index}`}><Highlighted text={paragraph} query={query} /></p>)}
            </section>
          ))}
        </section>
      ))}
      <footer className="document-end"><span />本章内容结束<span /></footer>
    </article>
  );
}
