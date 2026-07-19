"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/learning/icons";
import {
  ActiveDocument,
  BackendProject,
  DocumentProgress,
  GenerationJob,
  GenerationStatus,
  getBackendProject,
  getActiveDocument,
  getDocumentProgress,
  getGenerationJob,
  getLatestGenerationJob,
  getLatestDocument,
  getSyllabus,
  getLearningMaterial,
  saveSyllabus,
  startGeneration,
  uploadPDF,
} from "@/lib/revia-api";
import { isTransientNetworkError, SinglePromiseGate } from "@/lib/generation-reliability";
import { generationFailureCounts, generationFailureLabel, generationFailureReason, successfulGenerationCount } from "@/lib/generation-failures";
import { FileDropZone, SelectedPDF } from "./file-drop-zone";
import { SettingsTrigger } from "@/components/settings/settings-trigger";
import { useAuth } from "@/components/auth/auth-provider";

const terminalStatuses = new Set<GenerationStatus>(["completed", "partial_failed", "failed"]);
const progressLabels: Record<GenerationStatus, string> = {
  pending: "正在解析课程资料",
  parsing: "正在解析课程资料",
  matching: "正在匹配考试范围",
  generating: "正在生成复习材料",
  validating: "正在整理生成结果",
  completed: "复习材料生成完成",
  partial_failed: "复习材料生成完成，部分考纲未匹配",
  failed: "生成失败",
};

function conciseSyllabusItem(value: string) {
  return value
    .replace(/[：:]/g, "")
    .split(/[，,；;。]/, 1)[0]
    .replace(/(?:是)?核心考点|需掌握|重点掌握|无需死记硬背|理解为主/g, "")
    .trim();
}

function activeDocumentMessage(document: ActiveDocument) {
  return [
    "当前活动任务：",
    `项目名：${document.project_name}`,
    `文件名：${document.filename}`,
    `状态：${document.processing_status}`,
    `阶段：${document.processing_phase}`,
    `进度：${document.current_page} / ${document.total_pages || "?"} 页`,
  ].join(" ");
}

export function ProjectUploadPage({ projectId }: { projectId: string }) {
  const { role } = useAuth();
  const router = useRouter();
  const [project, setProject] = useState<BackendProject | null>(null);
  const [loadingProject, setLoadingProject] = useState(true);
  const [courseFiles, setCourseFiles] = useState<SelectedPDF[]>([]);
  const [syllabusFiles, setSyllabusFiles] = useState<SelectedPDF[]>([]);
  const [syllabusText, setSyllabusText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [visibleStatus, setVisibleStatus] = useState<GenerationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generationReconnecting, setGenerationReconnecting] = useState(false);
  const [documentProgress, setDocumentProgress] = useState<DocumentProgress | null>(null);
  const [uploadStage, setUploadStage] = useState<"uploading" | "processing" | null>(null);
  const generationStartRequestedRef = useRef(false);
  const generationStartGateRef = useRef(new SinglePromiseGate<GenerationJob>());
  const generationAttemptedAtRef = useRef(0);

  const openLearningMaterial = useCallback(async (finishedJob: GenerationJob) => {
    if (finishedJob.status !== "completed" && finishedJob.status !== "partial_failed") return;
    await getLearningMaterial(projectId);
    router.replace(`/projects/${projectId}/learn`);
  }, [projectId, router]);

  const beginJob = useCallback(async (regenerate: boolean) => {
    generationAttemptedAtRef.current = Date.now();
    const createdJob = await generationStartGateRef.current.run(() => startGeneration(projectId, regenerate));
    setGenerationReconnecting(false);
    setJob(createdJob);
    setVisibleStatus(createdJob.status);
    if (createdJob.status === "failed") setError(createdJob.error_message || "生成任务失败");
    if (createdJob.status === "completed") await openLearningMaterial(createdJob);
  }, [openLearningMaterial, projectId]);

  const resumeGenerationAfterParsing = useCallback(async () => {
    if (generationStartRequestedRef.current) return;
    generationStartRequestedRef.current = true;
    setGenerating(true);
    setUploadStage(null);
    setError(null);
    try {
      await beginJob(false);
    } catch (reason) {
      if (isTransientNetworkError(reason)) setGenerationReconnecting(true);
      else setError(reason instanceof Error ? reason.message : "生成流程启动失败");
    }
  }, [beginJob]);

  useEffect(() => {
    generationStartRequestedRef.current = false;
  }, [projectId]);

  useEffect(() => {
    let active = true;
    const restoreProject = async () => {
      try {
        const current = await getBackendProject(projectId);
        if (!active) return;
        setProject(current);
        if (current.status === "completed") {
          try {
            await getLearningMaterial(projectId);
            if (active) router.replace(`/projects/${projectId}/learn`);
          } catch {
            // Keep the upload page available if the completed project has no readable material.
          }
        }
      } catch {
        if (active) setProject(null);
      } finally {
        if (active) setLoadingProject(false);
      }
    };
    void restoreProject();
    return () => { active = false; };
  }, [projectId, router]);

  useEffect(() => {
    let active = true;
    getSyllabus(projectId)
      .then((syllabus) => {
        if (active && syllabus?.text) setSyllabusText(syllabus.text);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [projectId]);

  useEffect(() => {
    if (loadingProject || !project || project.status === "completed") return;
    let active = true;
    getLatestDocument(projectId, "course_material")
      .then((latest) => {
        if (!active || !latest) return;
        setDocumentProgress(latest);
        if (["queued", "processing", "parsing", "interrupted"].includes(latest.processing_status)) {
          setGenerating(true);
          setUploadStage("processing");
        }
        if (latest.processing_status === "parsed") {
          void resumeGenerationAfterParsing();
        }
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [loadingProject, project, projectId, resumeGenerationAfterParsing]);

  useEffect(() => {
    if (!documentProgress || !["queued", "processing", "parsing", "interrupted"].includes(documentProgress.processing_status)) return;
    let active = true;
    const timer = window.setTimeout(async () => {
      try {
        const current = await getDocumentProgress(projectId, documentProgress.id);
        if (!active) return;
        setDocumentProgress(current);
        if (current.processing_status === "failed") setError(current.error_message || "PDF 解析失败");
        if (current.processing_status === "parsed") {
          setUploadStage(null);
          void resumeGenerationAfterParsing();
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取解析进度");
      }
    }, 700);
    return () => { active = false; window.clearTimeout(timer); };
  }, [documentProgress, projectId, resumeGenerationAfterParsing]);

  useEffect(() => {
    if ((!job && !generationReconnecting) || (job && terminalStatuses.has(job.status))) return;
    let active = true;
    const animationTimers: number[] = [];
    let timer = 0;
    const poll = async () => {
      try {
        const current = job ? await getGenerationJob(projectId, job.id) : await getLatestGenerationJob(projectId);
        if (!active) return;
        if (!current || (!job && Date.parse(current.created_at) < generationAttemptedAtRef.current - 10_000)) {
          setGenerationReconnecting(true);
          timer = window.setTimeout(poll, 1000);
          return;
        }
        if (terminalStatuses.has(current.status)) {
          if (generationReconnecting) {
            setGenerationReconnecting(false);
            setJob(current);
            setVisibleStatus(current.status);
            if (current.status === "failed") setError(current.error_message || "生成任务失败");
            if (current.status === "completed") await openLearningMaterial(current);
            return;
          }
          const sequence = current.status_history.filter((status, index, values) => status !== "pending" && values.indexOf(status) === index);
          sequence.forEach((status, index) => {
            animationTimers.push(window.setTimeout(() => { if (active) setVisibleStatus(status); }, index * 240));
          });
          animationTimers.push(window.setTimeout(async () => {
            if (!active) return;
            setJob(current);
            if (current.status === "failed") setError(current.error_message || "生成任务失败");
            if (current.status === "completed") await openLearningMaterial(current);
          }, sequence.length * 240));
        } else {
          setGenerationReconnecting(false);
          setJob(current);
          setVisibleStatus(current.status);
          timer = window.setTimeout(poll, 300);
        }
      } catch {
        if (!active) return;
        setGenerationReconnecting(true);
        timer = window.setTimeout(poll, 1000);
      }
    };
    timer = window.setTimeout(poll, generationReconnecting ? 1000 : 300);
    return () => {
      active = false;
      window.clearTimeout(timer);
      animationTimers.forEach((animationTimer) => window.clearTimeout(animationTimer));
    };
  }, [generationReconnecting, job, openLearningMaterial, projectId]);

  const hasParsedCourse = documentProgress?.kind === "course_material" && documentProgress.processing_status === "parsed";
  const canGenerate = (courseFiles.length > 0 || hasParsedCourse) && (syllabusFiles.length > 0 || syllabusText.trim().length > 0);

  const generate = async () => {
    if (!project || (!submitted && !canGenerate)) return;
    setGenerating(true);
    setError(null);
    setGenerationReconnecting(false);
    try {
      const activeDocument = await getActiveDocument();
      if (activeDocument) {
        setUploadStage(null);
        setError(activeDocumentMessage(activeDocument));
        return;
      }
      generationStartRequestedRef.current = true;

      if (!submitted) {
        setUploadStage("uploading");
        if (syllabusText.trim()) await saveSyllabus(project.id, syllabusText, null);
        for (const selected of courseFiles) {
          await uploadPDF(project.id, "course_material", selected.file, (progress, stage) => {
            setDocumentProgress(progress);
            setUploadStage(stage);
          });
        }
        const syllabusDocumentId = syllabusFiles[0]
          ? await uploadPDF(project.id, "syllabus", syllabusFiles[0].file, (progress, stage) => {
              setDocumentProgress(progress);
              setUploadStage(stage);
            })
          : null;
        await saveSyllabus(project.id, syllabusText, syllabusDocumentId);
        setSubmitted(true);
        setUploadStage(null);
      }
      await beginJob(Boolean(job?.status === "failed" || submitted));
    } catch (reason) {
      setUploadStage(null);
      if (isTransientNetworkError(reason) && generationAttemptedAtRef.current > 0) {
        setGenerationReconnecting(true);
        return;
      }
      const message = reason instanceof Error ? reason.message : "生成流程启动失败";
      const activeDocument = await getActiveDocument().catch(() => null);
      setError(activeDocument ? activeDocumentMessage(activeDocument) : message);
    }
  };

  if (loadingProject) return <main className="entry-page entry-loading">正在读取项目…</main>;
  if (!project) return <main className="entry-page missing-project"><h1>未找到项目</h1><button onClick={() => router.push("/")}>返回项目列表</button></main>;
  if (role === "demo") return <main className="entry-page missing-project"><h1>演示模式不会处理资料</h1><p>请返回项目列表，选择已准备好的演示学习材料。</p><button onClick={() => router.push("/")}>返回项目列表</button></main>;

  const statusLabel = generationReconnecting
    ? "正在重新连接"
    : uploadStage === "uploading"
    ? "正在上传完整 PDF"
    : documentProgress?.processing_phase === "ocr"
      ? `正在 OCR 识别第 ${documentProgress.current_page || 0} / ${documentProgress.total_pages || "?"} 页`
    : documentProgress?.processing_phase === "resource_limited"
      ? "OCR 处理因服务器资源不足暂停，系统稍后可从当前页继续。"
    : documentProgress?.processing_status === "queued"
      ? role === "owner"
        ? "站长任务 · 已进入优先队列"
        : `排队中${documentProgress.queue_position ? `（当前第 ${documentProgress.queue_position} 位）` : ""}`
    : documentProgress?.processing_phase === "structuring"
      ? "正在整理内容结构"
      : documentProgress?.is_resuming
        ? `正在从第 ${Math.max(1, documentProgress.current_page)} 页继续`
        : uploadStage === "processing"
          ? `正在解析第 ${documentProgress?.current_page || 0} / ${documentProgress?.total_pages || "?"} 页`
          : visibleStatus
            ? progressLabels[visibleStatus]
            : "正在提交学习资料";
  const progressDetail = uploadStage && documentProgress
    ? `已完成文本提取 ${documentProgress.processed_pages} 页 · OCR ${documentProgress.ocr_page_count} 页 · 当前阶段：${documentProgress.processing_phase === "structuring" ? "内容整理" : documentProgress.processing_phase === "resource_limited" ? "等待资源恢复" : documentProgress.processing_phase === "ocr" ? "OCR 识别" : "解析"}`
    : job && job.total_items > 0
      ? `已处理 ${job.processed_items} / ${job.total_items} 项 · ${job.progress}%`
      : "正在上传、解析并组织学习内容，请稍候。";
  const partialJob = job?.status === "partial_failed" ? job : null;
  const completedItemCount = partialJob
    ? successfulGenerationCount(partialJob)
    : 0;
  const partialFailureCounts = partialJob ? generationFailureCounts(partialJob.item_failures) : null;

  return (
    <main className="entry-page upload-page">
      <header className="entry-header">
        <button className="entry-brand" onClick={() => router.push("/")}><span><Icon name="book" size={17} /></span>Revia</button>
        <div className="entry-header-actions"><p>{project.name}</p><SettingsTrigger variant="header" /></div>
      </header>
      <section className="upload-content">
        <button className="back-link" onClick={() => router.push("/")}>← 返回项目列表</button>
        <div className="upload-heading"><span className="entry-eyebrow">准备学习资料</span><h1>{project.name}</h1><p>上传课程资料并填写考纲，Revia 将据此生成结构化复习材料。</p></div>
        <div className="upload-section">
          <div className="upload-section-label"><span>01</span><div><h2>上传课程资料</h2><p>单个完整 PDF 最多 150MB、600 页，无需拆分；系统会按章节与内容结构自动解析。</p></div></div>
          <p className="upload-limit-note">{role === "owner"
            ? "站长工作区每次只能有一份资料排队或处理中，不受最近 24 小时页数额度限制。"
            : "每次只能有一份资料排队或处理中；最近 24 小时最多累计处理 1200 页。"}</p>
          {role === "owner" && <p className="owner-task-note">站长任务 · 提交后进入优先队列</p>}
          <FileDropZone title="拖拽完整课程资料到这里" hint="或点击选择文件，仅支持单个 PDF" kind="course_material" files={courseFiles} onFiles={setCourseFiles} />
        </div>
        <div className="upload-section">
          <div className="upload-section-label"><span>02</span><div><h2>填写考试范围</h2><p>上传考纲 PDF，或直接输入考纲内容。</p></div></div>
          <div className="syllabus-grid">
            <FileDropZone title="上传考纲" hint="拖拽或点击选择 PDF" kind="syllabus" files={syllabusFiles} onFiles={setSyllabusFiles} />
            <div className="syllabus-text"><label htmlFor="syllabus-text">直接输入考纲</label><textarea id="syllabus-text" value={syllabusText} onChange={(event) => setSyllabusText(event.target.value)} placeholder="例如：第三章外部性、公共物品；第四章财政政策……" /></div>
          </div>
        </div>
        <div className="generate-area"><p>{canGenerate ? "资料准备完成，可以开始生成。" : "请上传课程资料，并上传或填写考纲。"}</p><button className="entry-primary generate-button" disabled={!canGenerate || generating} onClick={generate}>{generating ? <><i />正在生成复习材料…</> : "开始生成"}</button></div>
      </section>
      {generating && <div className="generation-overlay" role="status"><div>{!error && !partialJob && <i />}<span>{statusLabel}</span><h2>{error ? "生成失败" : partialJob ? "复习材料已生成" : "生成复习材料"}</h2>{partialJob && partialFailureCounts ? <div className="generation-partial"><p>已完成 {completedItemCount} / {partialJob.total_items} 个考纲知识点的生成。</p><p>{partialFailureCounts.unmatched > 0 && `${partialFailureCounts.unmatched} 个未找到资料依据`}{partialFailureCounts.unmatched > 0 && partialFailureCounts.schema_validation > 0 && " · "}{partialFailureCounts.schema_validation > 0 && `${partialFailureCounts.schema_validation} 个未通过格式检查`}{partialFailureCounts.generation_error > 0 && ` · ${partialFailureCounts.generation_error} 个生成未完成`}。</p><details className="partial-failure-details"><summary>查看 {partialJob.item_failures.length} 个未生成考点及原因</summary><ul>{partialJob.item_failures.map((failure, index) => <li key={`${failure.position ?? index}-${failure.syllabus_item}`}><strong>{conciseSyllabusItem(failure.syllabus_item)}</strong><span>{generationFailureLabel(failure)} · {generationFailureReason(failure)}</span></li>)}</ul></details><button className="entry-primary generate-button" onClick={() => openLearningMaterial(partialJob)}>进入已生成的学习材料</button></div> : <p>{error || (generationReconnecting ? "连接暂时中断，正在继续查询已有任务，不会重复创建。" : progressDetail)}</p>}{error && <button className="entry-primary generate-button" onClick={generate}>重新生成</button>}</div></div>}
    </main>
  );
}
