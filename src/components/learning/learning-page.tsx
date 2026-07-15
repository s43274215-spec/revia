"use client";

import { MouseEvent, UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ContextMenu, MenuState } from "./context-menu";
import { KnowledgePoint, PointVersions, Project, Version, VersionPoint } from "./data";
import { DrawerState, OperationDrawer } from "./keyword-drawer";
import { ReadingContent } from "./reading-content";
import { OutlineSidebar, ProjectSidebar } from "./sidebars";
import { Toolbar } from "./toolbar";
import { getBackendProject, getLearningMaterial, listProjects } from "@/lib/revia-api";
import { toLearningProject, toProjectShell } from "@/lib/learning-material-adapter";

const tabs: { id: Version; label: string }[] = [{ id: "original", label: "原文" }, { id: "recitation", label: "背诵版" }, { id: "keywords", label: "关键词" }];
type History = { past: Project[][]; present: Project[]; future: Project[][] };

function updatePoint(projects: Project[], projectId: string, pointId: string, update: (point: KnowledgePoint) => KnowledgePoint) {
  return projects.map((project) => project.id !== projectId ? project : ({ ...project, chapters: project.chapters.map((chapter) => ({ ...chapter, points: chapter.points.map((point) => point.id === pointId ? update(point) : point) })) }));
}

export function LearningPage({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [history, setHistory] = useState<History>({ past: [], present: [], future: [] });
  const [activeProjectId, setActiveProjectId] = useState<string | null>(projectId);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState<Version>("original");
  const [query, setQuery] = useState("");
  const [drawer, setDrawer] = useState<DrawerState | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [readingProgress, setReadingProgress] = useState(0);
  const readingRef = useRef<HTMLDivElement>(null);
  const activeProject = history.present.find((project) => project.id === activeProjectId) ?? null;

  useEffect(() => {
    let active = true;
    Promise.resolve()
      .then(() => {
        if (active) { setLoading(true); setLoadError(null); }
        return Promise.all([getBackendProject(projectId), getLearningMaterial(projectId), listProjects()]);
      })
      .then(([project, material, projects]) => {
        if (!active) return;
        const current = toLearningProject(project, material);
        const sidebarProjects = projects
          .filter((item) => item.status === "completed")
          .map((item) => item.id === projectId ? current : toProjectShell(item));
        if (!sidebarProjects.some((item) => item.id === projectId)) sidebarProjects.unshift(current);
        setHistory({ past: [], present: sidebarProjects, future: [] });
        setActiveProjectId(projectId);
      })
      .catch((reason: unknown) => {
        if (active) setLoadError(reason instanceof Error ? reason.message : "无法读取学习材料");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { setDrawer(null); setMenu(null); } };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  const calculateProgress = useCallback((element = readingRef.current) => {
    if (!element) return;
    const scrollableDistance = element.scrollHeight - element.clientHeight;
    const percentage = scrollableDistance <= 0 ? 0 : Math.round((element.scrollTop / scrollableDistance) * 100);
    setReadingProgress(Math.max(0, Math.min(100, percentage)));
  }, []);

  useEffect(() => {
    const element = readingRef.current;
    if (!element) return;
    const frame = requestAnimationFrame(() => calculateProgress(element));
    const content = element.firstElementChild;
    const observer = new ResizeObserver(() => calculateProgress(element));
    observer.observe(element);
    if (content) observer.observe(content);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [activeProjectId, version, history.present, calculateProgress]);

  const commit = (next: Project[]) => setHistory((current) => ({ past: [...current.past, current.present], present: next, future: [] }));
  const undo = () => setHistory((current) => current.past.length ? ({ past: current.past.slice(0, -1), present: current.past.at(-1)!, future: [current.present, ...current.future] }) : current);
  const redo = () => setHistory((current) => current.future.length ? ({ past: [...current.past, current.present], present: current.future[0], future: current.future.slice(1) }) : current);
  const selectProject = (id: string) => {
    if (id !== projectId) {
      router.push(`/projects/${id}/learn`);
      return;
    }
    const readingArea = readingRef.current;
    if (readingArea) { readingArea.style.scrollBehavior = "auto"; readingArea.scrollTop = 0; }
    setActiveProjectId((current) => current === id ? null : id);
    setDrawer(null); setMenu(null); setQuery(""); setReadingProgress(0);
    if (readingArea) requestAnimationFrame(() => { readingArea.scrollTop = 0; readingArea.style.removeProperty("scroll-behavior"); calculateProgress(readingArea); });
  };
  const navigate = (id: string) => readingRef.current?.querySelector<HTMLElement>(`#${CSS.escape(id)}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const openContext = (event: MouseEvent, point: KnowledgePoint) => { event.preventDefault(); const width = 168; const height = 100; setMenu({ pointId: point.id, x: Math.min(event.clientX, window.innerWidth - width - 8), y: Math.min(event.clientY, window.innerHeight - height - 8) }); };
  const selectedPoint = activeProject?.chapters.flatMap((chapter) => chapter.points).find((point) => point.id === menu?.pointId);
  const edit = (mode: "single" | "global") => { if (selectedPoint) setDrawer({ mode, point: selectedPoint, version }); setMenu(null); };
  const remove = () => { if (!activeProject || !menu) return; const next = history.present.map((project) => project.id !== activeProject.id ? project : ({ ...project, chapters: project.chapters.map((chapter) => ({ ...chapter, points: chapter.points.filter((point) => point.id !== menu.pointId) })) })); commit(next); setMenu(null); };
  const saveSingle = (pointId: string, editedVersion: Version, value: VersionPoint) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions: { ...point.versions, [editedVersion]: value } }))); setDrawer(null); };
  const saveGlobal = (pointId: string, versions: PointVersions) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions }))); setDrawer(null); };

  return (
    <main className="learning-shell" onClick={() => setMenu(null)}>
      <ProjectSidebar projects={history.present} activeProjectId={activeProjectId} onSelect={selectProject} />
      {loading ? <section className="project-empty"><div><span>复习项目</span><h1>正在读取学习材料</h1><p>正在从 Revia 后端加载当前项目。</p></div></section> : loadError ? <section className="project-empty"><div><span>复习项目</span><h1>无法读取学习材料</h1><p>{loadError}</p></div></section> : !activeProject ? <section className="project-empty"><div><span>复习项目</span><h1>选择一个项目继续阅读</h1><p>从左侧项目列表进入对应的课程复习材料。</p></div></section> : activeProject.chapters.length === 0 ? <section className="project-empty"><div><span>复习项目</span><h1>暂无生成内容</h1><p>当前项目还没有可供阅读的学习材料。</p></div></section> : <>
        <OutlineSidebar project={activeProject} progress={readingProgress} onNavigate={navigate} />
        <section className="workspace">
          <Toolbar query={query} onQueryChange={setQuery} onExport={() => setExportOpen((value) => !value)} onUndo={undo} onRedo={redo} canUndo={history.past.length > 0} canRedo={history.future.length > 0} />
          {exportOpen && <div className="export-menu"><span>导出</span><button onClick={() => setExportOpen(false)}>Word</button></div>}
          <div className="version-bar" role="tablist" aria-label="内容版本">
            {tabs.map((tab) => <button role="tab" aria-selected={version === tab.id} className={version === tab.id ? "is-active" : ""} key={tab.id} onClick={() => { setVersion(tab.id); setDrawer(null); }}>{tab.label}</button>)}
          </div>
          <div className="reading-scroll" ref={readingRef} onScroll={(event: UIEvent<HTMLDivElement>) => calculateProgress(event.currentTarget)}><ReadingContent project={activeProject} version={version} query={query} onKeyword={(point) => { if (version === "keywords") setDrawer({ mode: "keyword", point }); }} onPointContext={openContext} /></div>
          {drawer && <OperationDrawer key={`${drawer.mode}-${drawer.point.id}-${drawer.mode === "keyword" ? "recitation" : drawer.version}`} state={drawer} onClose={() => setDrawer(null)} onSaveSingle={saveSingle} onSaveGlobal={saveGlobal} />}
        </section>
      </>}
      {menu && <ContextMenu menu={menu} onSingleEdit={() => edit("single")} onGlobalEdit={() => edit("global")} onDelete={remove} />}
    </main>
  );
}
