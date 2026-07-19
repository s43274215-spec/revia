"use client";

import { useEffect, useRef, useState } from "react";
import { Chapter, Project } from "./data";
import { Icon } from "./icons";
import { SettingsTrigger } from "@/components/settings/settings-trigger";
import { generationFailureLabel, generationFailureReason } from "@/lib/generation-failures";
import type { GenerationJob } from "@/lib/revia-api";

export function ProjectSidebar({ projects, activeProjectId, onSelect }: { projects: Project[]; activeProjectId: string | null; onSelect: (id: string) => void }) {
  return (
    <aside className="project-sidebar" aria-label="复习项目">
      <div className="brand"><span className="brand-mark"><Icon name="book" size={17} /></span><span>Revia</span></div>
      <p className="sidebar-label">复习项目</p>
      <nav className="project-list">
        {projects.map((project) => (
          <button key={project.id} className={`project-item ${activeProjectId === project.id ? "is-active" : ""}`} onClick={() => onSelect(project.id)}>
            <span className="project-initial">{project.name.slice(0, 1)}</span>
            <span><strong>{project.name}</strong><small>{project.meta}</small></span>
          </button>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <SettingsTrigger />
        <div className="sidebar-footer"><span className="status-dot" />学习材料 · 已保存</div>
      </div>
    </aside>
  );
}

export function OutlineSidebar({ project, progress, activeId, onNavigate, partialJob }: { project: Project; progress: number; activeId: string | null; onNavigate: (id: string) => void; partialJob: GenerationJob | null }) {
  const [expandedFailure, setExpandedFailure] = useState<number | null>(null);
  const itemRefs = useRef(new Map<string, HTMLButtonElement>());
  const failures = partialJob?.item_failures ?? [];

  useEffect(() => {
    if (activeId) itemRefs.current.get(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  const itemRef = (id: string) => (element: HTMLButtonElement | null) => {
    if (element) itemRefs.current.set(id, element);
    else { itemRefs.current.delete(id); }
  };

  return (
    <aside className="outline-sidebar" aria-label="本页目录">
      <div className="outline-heading"><p>{project.name}</p><span>课程目录</span></div>
      <nav className="outline-nav">
        {project.chapters.map((chapter: Chapter) => (
          <div className="outline-chapter" key={chapter.id}>
            {chapter.title && <button ref={itemRef(chapter.id)} className={activeId === chapter.id ? "is-active" : ""} aria-current={activeId === chapter.id ? "location" : undefined} onClick={() => onNavigate(chapter.id)}><span>{chapter.number}</span>{chapter.title}</button>}
            <div className="outline-points">
              {chapter.points.map((point) => <button ref={itemRef(point.id)} className={activeId === point.id ? "is-active" : ""} aria-current={activeId === point.id ? "location" : undefined} key={point.id} onClick={() => onNavigate(point.id)}>{point.title}</button>)}
            </div>
          </div>
        ))}
        {failures.length > 0 && <section className="outline-missing" aria-labelledby="missing-outline-title">
          <div className="outline-missing-heading"><strong id="missing-outline-title">未生成考点</strong><span>{failures.length}</span></div>
          <p>这些考点保留在目录中，点击可查看原因。</p>
          <ol>
            {failures.map((failure, index) => {
              const expanded = expandedFailure === index;
              return <li key={`${failure.position ?? index}-${failure.syllabus_item}`}>
                <button title={failure.syllabus_item} aria-expanded={expanded} onClick={() => setExpandedFailure(expanded ? null : index)}>
                  <span aria-hidden="true" />
                  <span>{failure.syllabus_item}</span>
                </button>
                {expanded && <div className="outline-missing-reason" role="status"><strong>{generationFailureLabel(failure)}</strong><p>{generationFailureReason(failure)}</p></div>}
              </li>;
            })}
          </ol>
        </section>}
      </nav>
      <div className="reading-progress">
        <span>阅读进度</span><strong>{progress}%</strong>
        <div role="progressbar" aria-label="阅读进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{ width: `${progress}%` }} /></div>
      </div>
    </aside>
  );
}
