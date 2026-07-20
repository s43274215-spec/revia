"use client";

import { MouseEvent as ReactMouseEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/learning/icons";
import { ActiveDocument, activeDocumentStatusLabel, BackendProject, cancelDocument, createProject, deleteProject, getActiveDocument, listProjects } from "@/lib/revia-api";
import { CreateProjectDialog } from "./create-project-dialog";
import { SettingsTrigger } from "@/components/settings/settings-trigger";
import { useAuth } from "@/components/auth/auth-provider";
import {
  clampProjectContextMenuPosition,
  PROJECT_CONTEXT_MENU_WIDTH,
  projectDeletionConfirmation,
  removeDeletedProject,
} from "@/lib/project-deletion";

const dateFormatter = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });
const statusLabels: Record<BackendProject["status"], string> = {
  not_uploaded: "未上传",
  processing: "处理中",
  completed: "已完成",
  failed: "处理失败",
};

type ProjectMenuState = {
  projectId: string;
  x: number;
  y: number;
};

function loadDashboardData(): Promise<[BackendProject[], ActiveDocument | null]> {
  return Promise.all([listProjects(), getActiveDocument()]);
}

export function ProjectDashboard() {
  const { role } = useAuth();
  const isDemo = role === "demo";
  const router = useRouter();
  const [projects, setProjects] = useState<BackendProject[]>([]);
  const [activeDocument, setActiveDocument] = useState<ActiveDocument | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancellingDocumentId, setCancellingDocumentId] = useState<string | null>(null);
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);
  const [projectMenu, setProjectMenu] = useState<ProjectMenuState | null>(null);
  const projectMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    const refreshProjects = () => {
      loadDashboardData().then(([items, currentDocument]) => {
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

  useEffect(() => {
    if (!projectMenu) return;

    const closeMenu = () => setProjectMenu(null);
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (projectMenuRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest("[data-project-menu-trigger]")) return;
      closeMenu();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    requestAnimationFrame(() => projectMenuRef.current?.querySelector<HTMLButtonElement>("button")?.focus());

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, [projectMenu]);

  const openProject = (project: BackendProject) => {
    if (isDemo && project.status !== "completed") {
      setError("演示模式只能浏览已准备好的学习材料，不会上传、解析或重新生成内容。");
      return;
    }
    router.push(project.status === "completed" ? `/projects/${project.id}/learn` : `/projects/${project.id}/upload`);
  };

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

  const cancelActiveDocument = async (document: ActiveDocument) => {
    const confirmed = window.confirm(
      `确定取消 ${document.filename} 吗？\n\n已完成页面不会删除。\n取消后不会自动继续处理。`,
    );
    if (!confirmed) return;
    setCancellingDocumentId(document.document_id);
    setError(null);
    try {
      await cancelDocument(document.project_id, document.document_id);
      const [items, currentDocument] = await loadDashboardData();
      setProjects(items);
      setActiveDocument(currentDocument);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法取消当前任务");
    } finally {
      setCancellingDocumentId(null);
    }
  };

  const removeProject = async (project: BackendProject) => {
    setProjectMenu(null);
    if (!window.confirm(projectDeletionConfirmation(project.name))) return;
    setDeletingProjectId(project.id);
    setError(null);
    try {
      await deleteProject(project.id);
      const next = removeDeletedProject(projects, activeDocument, project.id);
      setProjects(next.projects);
      setActiveDocument(next.activeDocument);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法删除项目");
    } finally {
      setDeletingProjectId(null);
    }
  };

  const openProjectMenuAt = (projectId: string, clientX: number, clientY: number) => {
    const position = clampProjectContextMenuPosition(
      clientX,
      clientY,
      window.innerWidth,
      window.innerHeight,
    );
    setProjectMenu({ projectId, ...position });
  };

  const openProjectContextMenu = (event: ReactMouseEvent<HTMLDivElement>, project: BackendProject) => {
    if (isDemo) return;
    event.preventDefault();
    openProjectMenuAt(project.id, event.clientX, event.clientY);
  };

  const toggleTouchProjectMenu = (event: ReactMouseEvent<HTMLButtonElement>, project: BackendProject) => {
    event.stopPropagation();
    if (projectMenu?.projectId === project.id) {
      setProjectMenu(null);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    openProjectMenuAt(project.id, rect.right - PROJECT_CONTEXT_MENU_WIDTH, rect.bottom + 6);
  };

  const contextProject = projectMenu
    ? projects.find((project) => project.id === projectMenu.projectId) ?? null
    : null;

  return (
    <main className="entry-page">
      <header className="entry-header">
        <div className="entry-brand"><span><Icon name="book" size={17} /></span>Revia</div>
        <div className="entry-header-actions"><p>复习项目</p><SettingsTrigger variant="header" /></div>
      </header>
      <section className="project-home">
        <div className="project-home-heading">
          <div><span className="entry-eyebrow">我的学习空间</span><h1>复习项目</h1><p>选择一门课程继续阅读，或创建新的复习项目。</p></div>
          <button className="entry-primary new-project-button" disabled={isDemo} title={isDemo ? "演示模式不能创建项目" : undefined} onClick={() => setDialogOpen(true)}><b>＋</b>{isDemo ? "演示模式只读" : "新建项目"}</button>
        </div>
        {activeDocument && <div className="project-table active-task-table" aria-label="当前活动任务">
          <div className="project-table-header"><span>当前活动任务</span><span>处理进度</span><span>当前状态</span><span /></div>
          <div className="project-row">
            <span className="project-course"><i>{activeDocument.project_name.slice(0, 1)}</i><span><strong>{activeDocument.project_name}</strong><small>{activeDocument.filename}</small>{activeDocument.error_message && <small className="active-task-error">{activeDocument.error_message}</small>}</span></span>
            <span>{activeDocument.processed_pages} / {activeDocument.total_pages || "?"} 页</span>
            <span><em className="project-status processing">{activeDocumentStatusLabel(activeDocument)}</em><small className="active-task-state">{activeDocument.processing_status} · {activeDocument.processing_phase}</small></span>
            <span className="active-task-actions">
              <button type="button" onClick={() => router.push(`/projects/${activeDocument.project_id}/upload`)}>查看进度&nbsp; →</button>
              {!isDemo && <button
                type="button"
                className="active-task-cancel"
                disabled={cancellingDocumentId === activeDocument.document_id}
                onClick={() => cancelActiveDocument(activeDocument)}
              >{cancellingDocumentId === activeDocument.document_id ? "正在取消…" : "取消任务"}</button>}
            </span>
          </div>
        </div>}
        <div className="project-table" aria-label="项目列表">
          <div className="project-table-header"><span>课程名称</span><span>创建时间</span><span>当前状态</span><span /></div>
          {error && <div className="project-row"><span className="project-course"><span><strong>无法连接后端</strong><small>{error}</small></span></span></div>}
          {projects.map((project) => (
            <div
              className="project-row project-row-with-menu"
              key={project.id}
              title={isDemo ? undefined : "右键打开项目操作"}
              onContextMenu={(event) => openProjectContextMenu(event, project)}
            >
              <span className="project-course"><i>{project.name.slice(0, 1)}</i><span><strong>{project.name}</strong><small>{project.description || "暂无课程描述"}</small></span></span>
              <span>{dateFormatter.format(new Date(project.created_at))}</span>
              <span><em className={`project-status ${project.status}`}>{statusLabels[project.status]}</em></span>
              <span className="project-actions">
                {!isDemo && <button
                  type="button"
                  className="project-more"
                  data-project-menu-trigger
                  aria-label={`更多项目操作：${project.name}`}
                  aria-haspopup="menu"
                  aria-expanded={projectMenu?.projectId === project.id}
                  disabled={deletingProjectId !== null}
                  onClick={(event) => toggleTouchProjectMenu(event, project)}
                ><span aria-hidden="true">···</span></button>}
                <button type="button" className="project-enter" disabled={deletingProjectId === project.id} onClick={() => openProject(project)}>进入项目&nbsp; →</button>
              </span>
            </div>
          ))}
        </div>
      </section>
      {contextProject && projectMenu && !isDemo && <div
        ref={projectMenuRef}
        className="project-context-menu"
        style={{ left: projectMenu.x, top: projectMenu.y }}
        role="menu"
        aria-label={`项目操作：${contextProject.name}`}
        data-project-context-menu
        onClick={(event) => event.stopPropagation()}
        onContextMenu={(event) => event.preventDefault()}
      >
        <button
          type="button"
          className="danger"
          role="menuitem"
          disabled={deletingProjectId !== null}
          onClick={() => void removeProject(contextProject)}
        >{deletingProjectId === contextProject.id ? "正在删除…" : "删除项目"}</button>
      </div>}
      <CreateProjectDialog open={dialogOpen} onClose={() => setDialogOpen(false)} onConfirm={create} />
    </main>
  );
}
