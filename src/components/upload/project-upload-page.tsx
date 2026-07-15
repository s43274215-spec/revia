"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/learning/icons";
import {
  BackendProject,
  GenerationJob,
  GenerationStatus,
  getBackendProject,
  getGenerationJob,
  getLearningMaterial,
  saveSyllabus,
  startGeneration,
  uploadPDF,
} from "@/lib/revia-api";
import { FileDropZone, SelectedPDF } from "./file-drop-zone";
import { SettingsTrigger } from "@/components/settings/settings-trigger";

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

export function ProjectUploadPage({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [project, setProject] = useState<BackendProject | null>(null);
  const [loadingProject, setLoadingProject] = useState(true);
  const [courseFiles, setCourseFiles] = useState<SelectedPDF[]>([]);
  const [syllabusFiles, setSyllabusFiles] = useState<SelectedPDF[]>([]);
  const [syllabusText, setSyllabusText] = useState("");
  const [generating, setGenerating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [visibleStatus, setVisibleStatus] = useState<GenerationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getBackendProject(projectId)
      .then((current) => { if (active) setProject(current); })
      .catch(() => { if (active) setProject(null); })
      .finally(() => { if (active) setLoadingProject(false); });
    return () => { active = false; };
  }, [projectId]);

  const openLearningMaterial = useCallback(async (finishedJob: GenerationJob) => {
    if (finishedJob.status !== "completed" && finishedJob.status !== "partial_failed") return;
    await getLearningMaterial(projectId);
    router.push(`/projects/${projectId}/learn`);
  }, [projectId, router]);

  useEffect(() => {
    if (!job || terminalStatuses.has(job.status)) return;
    let active = true;
    const animationTimers: number[] = [];
    const timer = window.setTimeout(async () => {
      try {
        const current = await getGenerationJob(projectId, job.id);
        if (!active) return;
        if (terminalStatuses.has(current.status)) {
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
          setJob(current);
          setVisibleStatus(current.status);
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取生成进度");
      }
    }, 300);
    return () => {
      active = false;
      window.clearTimeout(timer);
      animationTimers.forEach((animationTimer) => window.clearTimeout(animationTimer));
    };
  }, [job, openLearningMaterial, projectId]);

  const canGenerate = courseFiles.length > 0 && (syllabusFiles.length > 0 || syllabusText.trim().length > 0);

  const beginJob = async (regenerate: boolean) => {
    const createdJob = await startGeneration(projectId, regenerate);
    setJob(createdJob);
    setVisibleStatus(createdJob.status);
    if (createdJob.status === "failed") setError(createdJob.error_message || "生成任务失败");
    if (createdJob.status === "completed") {
      await openLearningMaterial(createdJob);
    }
  };

  const generate = async () => {
    if (!project || (!submitted && !canGenerate)) return;
    setGenerating(true);
    setError(null);
    try {
      if (!submitted) {
        setUploading(true);
        for (const selected of courseFiles) {
          await uploadPDF(project.id, "course_material", selected.file);
        }
        const syllabusDocumentId = syllabusFiles[0]
          ? await uploadPDF(project.id, "syllabus", syllabusFiles[0].file)
          : null;
        await saveSyllabus(project.id, syllabusText, syllabusDocumentId);
        setSubmitted(true);
        setUploading(false);
      }
      await beginJob(Boolean(job?.status === "failed" || submitted));
    } catch (reason) {
      setUploading(false);
      setError(reason instanceof Error ? reason.message : "生成流程启动失败");
    }
  };

  if (loadingProject) return <main className="entry-page entry-loading">正在读取项目…</main>;
  if (!project) return <main className="entry-page missing-project"><h1>未找到项目</h1><button onClick={() => router.push("/")}>返回项目列表</button></main>;

  const statusLabel = uploading ? "正在解析课程资料" : visibleStatus ? progressLabels[visibleStatus] : "正在提交学习资料";
  const progressDetail = job && job.total_items > 0
    ? `已处理 ${job.processed_items} / ${job.total_items} 项 · ${job.progress}%`
    : "正在上传、解析并组织学习内容，请稍候。";
  const partialJob = job?.status === "partial_failed" ? job : null;
  const completedItemCount = partialJob
    ? Math.max(0, partialJob.total_items - partialJob.item_failures.length)
    : 0;

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
          <div className="upload-section-label"><span>01</span><div><h2>上传课程资料</h2><p>支持一份或多份 PDF 课程资料。</p></div></div>
          <FileDropZone title="拖拽课程资料到这里" hint="或点击选择文件，仅支持 PDF" kind="course_material" multiple files={courseFiles} onFiles={setCourseFiles} />
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
      {generating && <div className="generation-overlay" role="status"><div>{!error && !partialJob && <i />}<span>{statusLabel}</span><h2>{error ? "生成失败" : partialJob ? "复习材料已生成" : "生成复习材料"}</h2>{partialJob ? <div className="generation-partial"><p>已完成 {completedItemCount} 个考纲知识点的生成。</p><p>{partialJob.item_failures.length} 个知识点未在资料中找到足够依据：</p><ul>{partialJob.item_failures.map((failure) => <li key={failure.syllabus_item}>{conciseSyllabusItem(failure.syllabus_item)}</li>)}</ul><button className="entry-primary generate-button" onClick={() => openLearningMaterial(partialJob)}>进入已生成的学习材料</button></div> : <p>{error || progressDetail}</p>}{error && <button className="entry-primary generate-button" onClick={generate}>重新生成</button>}</div></div>}
    </main>
  );
}
