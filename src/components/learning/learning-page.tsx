"use client";

import { MouseEvent, UIEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ContextMenu, MenuState } from "./context-menu";
import { BulletPoint, PointVersions, Project, Version, VersionPoint } from "./data";
import { DrawerState, OperationDrawer } from "./keyword-drawer";
import { ReadingContent } from "./reading-content";
import { OutlineSidebar, ProjectSidebar } from "./sidebars";
import { Toolbar } from "./toolbar";
import { GenerationJob, GenerationStatus, getBackendProject, getGenerationJob, getLearningMaterial, listProjects, startGeneration } from "@/lib/revia-api";
import { toLearningProject, toProjectShell } from "@/lib/learning-material-adapter";

const tabs: { id: Version; label: string }[] = [{ id: "original", label: "原文" }, { id: "recitation", label: "背诵版" }, { id: "keywords", label: "关键词" }];
type History = { past: Project[][]; present: Project[]; future: Project[][] };
const terminalGenerationStatuses = new Set<GenerationStatus>(["completed", "partial_failed", "failed"]);
const generationStatusLabels: Record<GenerationStatus, string> = {
  pending: "正在准备重新生成",
  parsing: "正在解析课程资料",
  matching: "正在匹配考试范围",
  generating: "正在重新生成学习材料",
  validating: "正在整理生成结果",
  completed: "学习材料重新生成完成",
  partial_failed: "学习材料已重新生成，部分考纲未匹配",
  failed: "重新生成失败",
};

function updatePoint(projects: Project[], projectId: string, pointId: string, update: (point: BulletPoint) => BulletPoint) {
  return projects.map((project) => project.id !== projectId ? project : ({ ...project, chapters: project.chapters.map((chapter) => ({ ...chapter, points: chapter.points.map((knowledgePoint) => ({ ...knowledgePoint, bulletPoints: knowledgePoint.bulletPoints.map((point) => point.id === pointId ? update(point) : point) })) })) }));
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
  const [generationJob, setGenerationJob] = useState<GenerationJob | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [readingProgress, setReadingProgress] = useState(0);
  const readingRef = useRef<HTMLDivElement>(null);
  const activeProject = history.present.find((project) => project.id === activeProjectId) ?? null;

  const loadProjects = useCallback(async () => {
    const [project, material, projects] = await Promise.all([getBackendProject(projectId), getLearningMaterial(projectId), listProjects()]);
    const current = toLearningProject(project, material);
    const sidebarProjects = projects
      .filter((item) => item.status === "completed")
      .map((item) => item.id === projectId ? current : toProjectShell(item));
    if (!sidebarProjects.some((item) => item.id === projectId)) sidebarProjects.unshift(current);
    setHistory({ past: [], present: sidebarProjects, future: [] });
    setActiveProjectId(projectId);
  }, [projectId]);

  useEffect(() => {
    let active = true;
    Promise.resolve()
      .then(() => {
        if (active) { setLoading(true); setLoadError(null); }
        return loadProjects();
      })
      .then(() => undefined)
      .catch((reason: unknown) => {
        if (active) setLoadError(reason instanceof Error ? reason.message : "无法读取学习材料");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [loadProjects]);

  useEffect(() => {
    if (!generationJob || terminalGenerationStatuses.has(generationJob.status)) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const current = await getGenerationJob(projectId, generationJob.id);
        if (!active) return;
        setGenerationJob(current);
        if (current.status === "failed") setGenerationError(current.error_message || "重新生成任务失败");
        if (current.status === "completed" || current.status === "partial_failed") {
          await loadProjects();
          if (active) setGenerationJob(null);
        }
      } catch (reason) {
        if (active) setGenerationError(reason instanceof Error ? reason.message : "无法读取生成进度");
      }
    }, 300);
    return () => { active = false; window.clearTimeout(timer); };
  }, [generationJob, loadProjects, projectId]);

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
  const openContext = (event: MouseEvent, point: BulletPoint) => { event.preventDefault(); const width = 168; const height = 100; setMenu({ pointId: point.id, x: Math.min(event.clientX, window.innerWidth - width - 8), y: Math.min(event.clientY, window.innerHeight - height - 8) }); };
  const selectedPoint = activeProject?.chapters.flatMap((chapter) => chapter.points.flatMap((point) => point.bulletPoints)).find((point) => point.id === menu?.pointId);
  const edit = (mode: "single" | "global") => { if (selectedPoint) setDrawer({ mode, point: selectedPoint, version }); setMenu(null); };
  const remove = () => { if (!activeProject || !menu) return; const next = history.present.map((project) => project.id !== activeProject.id ? project : ({ ...project, chapters: project.chapters.map((chapter) => ({ ...chapter, points: chapter.points.map((point) => ({ ...point, bulletPoints: point.bulletPoints.filter((bullet) => bullet.id !== menu.pointId) })).filter((point) => point.bulletPoints.length > 0) })) })); commit(next); setMenu(null); };
  const saveSingle = (pointId: string, editedVersion: Version, value: VersionPoint) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions: { ...point.versions, [editedVersion]: value } }))); setDrawer(null); };
  const saveGlobal = (pointId: string, versions: PointVersions) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions }))); setDrawer(null); };
  const regenerate = async () => {
    if (generationJob && !generationError) return;
    setGenerationError(null);
    try {
      const job = await startGeneration(projectId, true);
      setGenerationJob(job);
      if (job.status === "failed") setGenerationError(job.error_message || "重新生成任务失败");
      if (job.status === "completed" || job.status === "partial_failed") {
        await loadProjects();
        setGenerationJob(null);
      }
    } catch (reason) {
      setGenerationError(reason instanceof Error ? reason.message : "无法启动重新生成");
    }
  };

  return (
    <main className="learning-shell" onClick={() => setMenu(null)}>
      <ProjectSidebar projects={history.present} activeProjectId={activeProjectId} onSelect={selectProject} />
      {loading ? <section className="project-empty"><div><span>复习项目</span><h1>正在读取学习材料</h1><p>正在从 Revia 后端加载当前项目。</p></div></section> : loadError ? <section className="project-empty"><div><span>复习项目</span><h1>无法读取学习材料</h1><p>{loadError}</p></div></section> : !activeProject ? <section className="project-empty"><div><span>复习项目</span><h1>选择一个项目继续阅读</h1><p>从左侧项目列表进入对应的课程复习材料。</p></div></section> : activeProject.chapters.length === 0 ? <section className="project-empty"><div><span>复习项目</span><h1>暂无生成内容</h1><p>当前项目还没有可供阅读的学习材料。</p></div></section> : <>
        <OutlineSidebar project={activeProject} progress={readingProgress} onNavigate={navigate} />
        <section className="workspace">
          <Toolbar query={query} onQueryChange={setQuery} onExport={() => setExportOpen((value) => !value)} onRegenerate={regenerate} regenerating={Boolean(generationJob)} onUndo={undo} onRedo={redo} canUndo={history.past.length > 0} canRedo={history.future.length > 0} />
          {exportOpen && <div className="export-menu"><span>导出</span><button onClick={() => setExportOpen(false)}>Word</button></div>}
          <div className="version-bar" role="tablist" aria-label="内容版本">
            {tabs.map((tab) => <button role="tab" aria-selected={version === tab.id} className={version === tab.id ? "is-active" : ""} key={tab.id} onClick={() => { setVersion(tab.id); setDrawer(null); }}>{tab.label}</button>)}
          </div>
          <div className="reading-scroll" ref={readingRef} onScroll={(event: UIEvent<HTMLDivElement>) => calculateProgress(event.currentTarget)}><ReadingContent project={activeProject} version={version} query={query} onKeyword={(point) => { if (version === "keywords") setDrawer({ mode: "keyword", point }); }} onPointContext={openContext} /></div>
          {drawer && <OperationDrawer key={`${drawer.mode}-${drawer.point.id}-${drawer.mode === "keyword" ? "recitation" : drawer.version}`} state={drawer} onClose={() => setDrawer(null)} onSaveSingle={saveSingle} onSaveGlobal={saveGlobal} />}
        </section>
      </>}
      {menu && <ContextMenu menu={menu} onSingleEdit={() => edit("single")} onGlobalEdit={() => edit("global")} onDelete={remove} />}
      {(generationJob || generationError) && <div className="generation-overlay" role="status"><div>{generationJob && !generationError && <i />}<span>{generationError ? "重新生成失败" : generationStatusLabels[generationJob!.status]}</span><h2>{generationError ? "无法重新生成学习材料" : "重新生成学习材料"}</h2><p>{generationError || (generationJob && generationJob.total_items > 0 ? `已处理 ${generationJob.processed_items} / ${generationJob.total_items} 项 · ${generationJob.progress}%` : "正在基于现有资料生成新的学习材料，请稍候。")}</p>{generationError && <div className="generation-error-actions"><button onClick={() => { setGenerationJob(null); setGenerationError(null); }}>取消</button><button className="entry-primary" onClick={() => { setGenerationJob(null); void regenerate(); }}>重试</button></div>}</div></div>}
    </main>
  );
}
