import { Chapter, Project } from "./data";
import { Icon } from "./icons";
import { SettingsTrigger } from "@/components/settings/settings-trigger";

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

export function OutlineSidebar({ project, progress, onNavigate }: { project: Project; progress: number; onNavigate: (id: string) => void }) {
  return (
    <aside className="outline-sidebar" aria-label="本页目录">
      <div className="outline-heading"><p>{project.name}</p><span>课程目录</span></div>
      <nav className="outline-nav">
        {project.chapters.map((chapter: Chapter) => (
          <div className="outline-chapter" key={chapter.id}>
            <button onClick={() => onNavigate(chapter.id)}><span>{chapter.number}</span>{chapter.title}</button>
            <div className="outline-points">
              {chapter.points.map((point) => <button key={point.id} onClick={() => onNavigate(point.id)}>{point.versions.original.title}</button>)}
            </div>
          </div>
        ))}
      </nav>
      <div className="reading-progress">
        <span>阅读进度</span><strong>{progress}%</strong>
        <div role="progressbar" aria-label="阅读进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><i style={{ width: `${progress}%` }} /></div>
      </div>
    </aside>
  );
}
