"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/learning/icons";
import { ActiveDocument, activeDocumentStatusLabel, BackendProject, createProject, getActiveDocument, listProjects } from "@/lib/revia-api";
import { CreateProjectDialog } from "./create-project-dialog";
import { SettingsTrigger } from "@/components/settings/settings-trigger";

const dateFormatter = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });
const statusLabels: Record<BackendProject["status"], string> = {
  not_uploaded: "未上传",
  processing: "处理中",
  completed: "已完成",
  failed: "处理失败",
};

export function ProjectDashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<BackendProject[]>([]);
  const [activeDocument, setActiveDocument] = useState<ActiveDocument | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const refreshProjects = () => {
      Promise.all([listProjects(), getActiveDocument()]).then(([items, currentDocument]) => {
        if (!active) return;
        setProjects(items);
        setActiveDocument(currentDocument);
        setError(null);
      }).catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取项目列表");
      });
    };
    refreshProjects();
    window.addEventListener("focus", refreshProjects);
    return () => {
      active = false;
      window.removeEventListener("focus", refreshProjects);
    };
  }, []);

  const openProject = (project: BackendProject) => router.push(project.status === "completed" ? `/projects/${project.id}/learn` : `/projects/${project.id}/upload`);
  const create = async (value: { name: string; description: string }) => {
    try {
      setError(null);
      const project = await createProject(value);
      setProjects((current) => [project, ...current]);
      setDialogOpen(false);
      router.push(`/projects/${project.id}/upload`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目创建失败");
    }
  };

  return (
    <main className="entry-page">
      <header className="entry-header">
        <div className="entry-brand"><span><Icon name="book" size={17} /></span>Revia</div>
        <div className="entry-header-actions"><p>复习项目</p><SettingsTrigger variant="header" /></div>
      </header>
      <section className="project-home">
        <div className="project-home-heading">
          <div><span className="entry-eyebrow">我的学习空间</span><h1>复习项目</h1><p>选择一门课程继续阅读，或创建新的复习项目。</p></div>
          <button className="entry-primary new-project-button" onClick={() => setDialogOpen(true)}><b>＋</b>新建项目</button>
        </div>
        {activeDocument && <div className="project-table active-task-table" aria-label="当前活动任务">
          <div className="project-table-header"><span>当前活动任务</span><span>处理进度</span><span>当前状态</span><span /></div>
          <button className="project-row" onClick={() => router.push(`/projects/${activeDocument.project_id}/upload`)}>
            <span className="project-course"><i>{activeDocument.project_name.slice(0, 1)}</i><span><strong>{activeDocument.project_name}</strong><small>{activeDocument.filename}</small></span></span>
            <span>{activeDocument.processed_pages} / {activeDocument.total_pages || "?"} 页</span>
            <span><em className="project-status processing">{activeDocumentStatusLabel(activeDocument)}</em></span>
            <span className="project-arrow">查看进度&nbsp; →</span>
          </button>
        </div>}
        <div className="project-table" aria-label="项目列表">
          <div className="project-table-header"><span>课程名称</span><span>创建时间</span><span>当前状态</span><span /></div>
          {error && <div className="project-row"><span className="project-course"><span><strong>无法连接后端</strong><small>{error}</small></span></span></div>}
          {projects.map((project) => (
            <button className="project-row" key={project.id} onClick={() => openProject(project)}>
              <span className="project-course"><i>{project.name.slice(0, 1)}</i><span><strong>{project.name}</strong><small>{project.description || "暂无课程描述"}</small></span></span>
              <span>{dateFormatter.format(new Date(project.created_at))}</span>
              <span><em className={`project-status ${project.status}`}>{statusLabels[project.status]}</em></span>
              <span className="project-arrow">进入项目&nbsp; →</span>
            </button>
          ))}
        </div>
      </section>
      <CreateProjectDialog open={dialogOpen} onClose={() => setDialogOpen(false)} onConfirm={create} />
    </main>
  );
}
