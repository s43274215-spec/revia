"use client";

import { MouseEvent, UIEvent, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ContextMenu, MenuState } from "./context-menu";
import { BulletPoint, PointVersions, Project, Version, VersionPoint, versionContract } from "./data";
import { DrawerState, OperationDrawer } from "./keyword-drawer";
import { ReadingContent } from "./reading-content";
import { OutlineSidebar, ProjectSidebar } from "./sidebars";
import { Toolbar } from "./toolbar";
import { SearchResults } from "./search-results";
import { searchProject } from "./reader-search";
import { classifyContentBlocks } from "./content-format";
import { GenerationJob, GenerationStatus, downloadWordExport, getBackendProject, getGenerationJob, getLatestGenerationJob, getLatestPublishedGenerationJob, getLearningMaterial, listProjects, startGeneration } from "@/lib/revia-api";
import { isTransientNetworkError, SinglePromiseGate } from "@/lib/generation-reliability";
import { toLearningProject, toProjectShell } from "@/lib/learning-material-adapter";
import { useAuth } from "@/components/auth/auth-provider";

const tabs: readonly { id: Version; label: string }[] = versionContract;
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
  const { role } = useAuth();
  const isDemo = role === "demo";
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
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [generationJob, setGenerationJob] = useState<GenerationJob | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  const [generationStarting, setGenerationStarting] = useState(false);
  const [generationReconnecting, setGenerationReconnecting] = useState(false);
  const [partialGenerationJob, setPartialGenerationJob] = useState<GenerationJob | null>(null);
  const [readingProgress, setReadingProgress] = useState(0);
  const [activeOutlineId, setActiveOutlineId] = useState<string | null>(null);
  const [activeSearchIndex, setActiveSearchIndex] = useState(0);
  const readingRef = useRef<HTMLDivElement>(null);
  const navigationSuppressedUntilRef = useRef(0);
  const generationStartGateRef = useRef(new SinglePromiseGate<GenerationJob>());
  const generationAttemptedAtRef = useRef(0);
  const activeProject = history.present.find((project) => project.id === activeProjectId) ?? null;
  const deferredQuery = useDeferredValue(query.trim());
  const effectiveQuery = query.trim() ? deferredQuery : "";
  const searchResults = useMemo(
    () => activeProject ? searchProject(activeProject, version, effectiveQuery, classifyContentBlocks) : [],
    [activeProject, effectiveQuery, version],
  );
  const activeSearchTarget = searchResults[activeSearchIndex]?.targetId;

  const loadProjects = useCallback(async () => {
    const [project, material, projects, latestPublishedJob] = await Promise.all([
      getBackendProject(projectId),
      getLearningMaterial(projectId),
      listProjects(),
      getLatestPublishedGenerationJob(projectId).catch(() => null),
    ]);
    const current = toLearningProject(project, material);
    const sidebarProjects = projects
      .filter((item) => item.status === "completed")
      .map((item) => item.id === projectId ? current : toProjectShell(item));
    if (!sidebarProjects.some((item) => item.id === projectId)) sidebarProjects.unshift(current);
    setHistory({ past: [], present: sidebarProjects, future: [] });
    setActiveProjectId(projectId);
    setPartialGenerationJob(latestPublishedJob?.status === "partial_failed" ? latestPublishedJob : null);
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
    if ((!generationJob && !generationReconnecting) || (generationJob && terminalGenerationStatuses.has(generationJob.status))) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const current = generationJob
          ? await getGenerationJob(projectId, generationJob.id)
          : await getLatestGenerationJob(projectId);
        if (!active) return;
        if (!current || (!generationJob && Date.parse(current.created_at) < generationAttemptedAtRef.current - 10_000)) {
          setGenerationReconnecting(true);
          timer = window.setTimeout(poll, 1000);
          return;
        }
        if (current.status === "failed") {
          setGenerationJob(current);
          setGenerationReconnecting(false);
          setGenerationError(current.error_message || "重新生成任务失败");
          return;
        }
        if (current.status === "completed" || current.status === "partial_failed") {
          await loadProjects();
          if (active) {
            setGenerationJob(null);
            setGenerationReconnecting(false);
          }
          return;
        }
        setGenerationJob(current);
        setGenerationReconnecting(false);
        timer = window.setTimeout(poll, 300);
      } catch {
        if (!active) return;
        setGenerationReconnecting(true);
        timer = window.setTimeout(poll, 1000);
      }
    };
    timer = window.setTimeout(poll, generationReconnecting ? 1000 : 300);
    return () => { active = false; window.clearTimeout(timer); };
  }, [generationJob, generationReconnecting, loadProjects, projectId]);

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

  useEffect(() => {
    const root = readingRef.current;
    if (!root || !activeProject) return;
    const visible = new Map<Element, IntersectionObserverEntry>();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => entry.isIntersecting ? visible.set(entry.target, entry) : visible.delete(entry.target));
      if (Date.now() < navigationSuppressedUntilRef.current || visible.size === 0) return;
      const next = [...visible.values()].sort((left, right) => {
        const distance = Math.abs(left.boundingClientRect.top - (left.rootBounds?.top ?? 0));
        const otherDistance = Math.abs(right.boundingClientRect.top - (right.rootBounds?.top ?? 0));
        if (distance !== otherDistance) return distance - otherDistance;
        return Number(right.target.classList.contains("knowledge-section")) - Number(left.target.classList.contains("knowledge-section"));
      })[0];
      if ((next.target as HTMLElement).id) setActiveOutlineId((next.target as HTMLElement).id);
    }, { root, rootMargin: "-10% 0px -72% 0px", threshold: [0, 0.01, 1] });
    root.querySelectorAll<HTMLElement>(".chapter-section,.knowledge-section").forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [activeProject, version]);

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
    setDrawer(null); setMenu(null); setQuery(""); setActiveSearchIndex(0); setReadingProgress(0);
    if (readingArea) requestAnimationFrame(() => { readingArea.scrollTop = 0; readingArea.style.removeProperty("scroll-behavior"); calculateProgress(readingArea); });
  };
  const scrollToTarget = (id: string) => {
    const readingArea = readingRef.current;
    const target = readingArea?.querySelector<HTMLElement>(`#${CSS.escape(id)}`);
    if (!readingArea || !target) return;
    const top = target.getBoundingClientRect().top - readingArea.getBoundingClientRect().top + readingArea.scrollTop - 24;
    readingArea.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  };
  const navigate = (id: string) => {
    navigationSuppressedUntilRef.current = Date.now() + 900;
    setActiveOutlineId(id);
    scrollToTarget(id);
  };
  const selectSearchResult = (index: number) => {
    if (!searchResults.length) return;
    const nextIndex = (index + searchResults.length) % searchResults.length;
    const result = searchResults[nextIndex];
    setActiveSearchIndex(nextIndex);
    navigationSuppressedUntilRef.current = Date.now() + 900;
    const target = readingRef.current?.querySelector<HTMLElement>(`#${CSS.escape(result.targetId)}`);
    const outlineTarget = target?.closest<HTMLElement>(".knowledge-section,.chapter-section");
    if (outlineTarget?.id) setActiveOutlineId(outlineTarget.id);
    scrollToTarget(result.targetId);
  };
  const openContext = (event: MouseEvent, point: BulletPoint) => { event.preventDefault(); const width = 168; const height = 100; setMenu({ pointId: point.id, x: Math.min(event.clientX, window.innerWidth - width - 8), y: Math.min(event.clientY, window.innerHeight - height - 8) }); };
  const selectedPoint = activeProject?.chapters.flatMap((chapter) => chapter.points.flatMap((point) => point.bulletPoints)).find((point) => point.id === menu?.pointId);
  const edit = (mode: "single" | "global") => { if (selectedPoint) setDrawer({ mode, point: selectedPoint, version }); setMenu(null); };
  const remove = () => { if (!activeProject || !menu) return; const next = history.present.map((project) => project.id !== activeProject.id ? project : ({ ...project, chapters: project.chapters.map((chapter) => ({ ...chapter, points: chapter.points.map((point) => ({ ...point, bulletPoints: point.bulletPoints.filter((bullet) => bullet.id !== menu.pointId) })).filter((point) => point.bulletPoints.length > 0) })) })); commit(next); setMenu(null); };
  const saveSingle = (pointId: string, editedVersion: Version, value: VersionPoint) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions: { ...point.versions, [editedVersion]: value } }))); setDrawer(null); };
  const saveGlobal = (pointId: string, versions: PointVersions) => { if (!activeProject) return; commit(updatePoint(history.present, activeProject.id, pointId, (point) => ({ ...point, versions }))); setDrawer(null); };
  const regenerate = async () => {
    if (isDemo) return;
    if ((generationJob && generationJob.status !== "failed") || generationReconnecting || generationStartGateRef.current.pending) return;
    generationAttemptedAtRef.current = Date.now();
    setGenerationJob(null);
    setGenerationError(null);
    setGenerationStarting(true);
    try {
      const job = await generationStartGateRef.current.run(() => startGeneration(projectId, true));
      setGenerationJob(job);
      if (job.status === "failed") setGenerationError(job.error_message || "重新生成任务失败");
      if (job.status === "completed" || job.status === "partial_failed") {
        await loadProjects();
        setGenerationJob(null);
      }
    } catch (reason) {
      if (isTransientNetworkError(reason)) setGenerationReconnecting(true);
      else setGenerationError(reason instanceof Error ? reason.message : "无法启动重新生成");
    } finally {
      setGenerationStarting(false);
    }
  };

  const exportWord = async (scope: "current" | "all") => {
    if (!activeProject || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      await downloadWordExport(activeProject.id, scope === "all" ? "all" : version);
      setExportOpen(false);
    } catch (reason) {
      setExportError(reason instanceof Error ? reason.message : "Word 导出失败");
    } finally {
      setExporting(false);
    }
  };

  return (
    <main className="learning-shell" onClick={() => setMenu(null)}>
      <ProjectSidebar projects={history.present} activeProjectId={activeProjectId} onSelect={selectProject} />
      {loading ? <section className="project-empty"><div><span>复习项目</span><h1>正在读取学习材料</h1><p>正在从 Revia 后端加载当前项目。</p></div></section> : loadError ? <section className="project-empty"><div><span>复习项目</span><h1>无法读取学习材料</h1><p>{loadError}</p></div></section> : !activeProject ? <section className="project-empty"><div><span>复习项目</span><h1>选择一个项目继续阅读</h1><p>从左侧项目列表进入对应的课程复习材料。</p></div></section> : activeProject.chapters.length === 0 ? <section className="project-empty"><div><span>复习项目</span><h1>暂无生成内容</h1><p>当前项目还没有可供阅读的学习材料。</p></div></section> : <>
        <OutlineSidebar project={activeProject} progress={readingProgress} activeId={activeOutlineId} onNavigate={navigate} partialJob={partialGenerationJob} />
        <section className="workspace">
          <Toolbar query={query} onQueryChange={(value) => { setQuery(value); setActiveSearchIndex(0); }} onExport={() => { setExportOpen((value) => !value); setExportError(null); }} onRegenerate={regenerate} regenerating={Boolean(generationJob) || generationStarting || generationReconnecting} regenerationDisabled={isDemo} onUndo={undo} onRedo={redo} canUndo={history.past.length > 0} canRedo={history.future.length > 0} />
          <SearchResults query={effectiveQuery} results={searchResults} activeIndex={activeSearchIndex} onSelect={selectSearchResult} onPrevious={() => selectSearchResult(activeSearchIndex - 1)} onNext={() => selectSearchResult(activeSearchIndex + 1)} onClear={() => { setQuery(""); setActiveSearchIndex(0); }} />
          {exportOpen && <div className="export-menu"><span>导出 Word</span><button disabled={exporting} onClick={() => void exportWord("current")}>导出当前版本</button><button disabled={exporting} onClick={() => void exportWord("all")}>导出全部版本</button>{exportError && <p role="alert">{exportError}</p>}</div>}
          <div className="version-bar" role="tablist" aria-label="内容版本">
            {tabs.map((tab) => <button role="tab" aria-selected={version === tab.id} className={version === tab.id ? "is-active" : ""} key={tab.id} onClick={() => { setVersion(tab.id); setActiveSearchIndex(0); setDrawer(null); }}>{tab.label}</button>)}
          </div>
          <div className="reading-scroll" ref={readingRef} onScroll={(event: UIEvent<HTMLDivElement>) => calculateProgress(event.currentTarget)}><ReadingContent project={activeProject} version={version} query={effectiveQuery} activeTargetId={activeSearchTarget} partialJob={partialGenerationJob} onKeyword={(point) => { if (version === "keywords") setDrawer({ mode: "keyword", point }); }} onPointContext={openContext} /></div>
          {drawer && <OperationDrawer key={`${drawer.mode}-${drawer.point.id}-${drawer.mode === "keyword" ? "recitation" : drawer.version}`} state={drawer} demoMode={isDemo} onClose={() => setDrawer(null)} onSaveSingle={saveSingle} onSaveGlobal={saveGlobal} />}
        </section>
      </>}
      {menu && <ContextMenu menu={menu} readOnly={isDemo} onSingleEdit={() => edit("single")} onGlobalEdit={() => edit("global")} onDelete={remove} />}
      {(generationJob || generationError || generationStarting || generationReconnecting) && <div className="generation-overlay" role="status"><div>{!generationError && <i />}<span>{generationError ? "重新生成失败" : generationReconnecting ? "正在重新连接" : generationStarting ? "正在提交重新生成任务" : generationStatusLabels[generationJob!.status]}</span><h2>{generationError ? "无法重新生成学习材料" : "重新生成学习材料"}</h2><p>{generationError || (generationReconnecting ? "连接暂时中断，正在继续查询已有任务，不会重复创建。" : generationJob && generationJob.total_items > 0 ? `已处理 ${generationJob.processed_items} / ${generationJob.total_items} 项 · ${generationJob.progress}%` : "正在基于现有资料生成新的学习材料，请稍候。")}</p>{generationError && <div className="generation-error-actions"><button onClick={() => { setGenerationJob(null); setGenerationError(null); }}>取消</button><button className="entry-primary" onClick={() => { void regenerate(); }}>重试</button></div>}</div></div>}
    </main>
  );
}
