"use client";

import { FormEvent, useEffect, useState } from "react";
import type { BackendProject } from "@/lib/revia-api";
import {
  normalizeProjectEditValue,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_NAME_MAX_LENGTH,
  type ProjectEditValue,
} from "@/lib/project-editing";

type EditProjectDialogProps = {
  project: BackendProject;
  onClose: () => void;
  onConfirm: (projectId: string, value: ProjectEditValue) => Promise<void>;
};

export function EditProjectDialog({ project, onClose, onConfirm }: EditProjectDialogProps) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [saving, onClose]);

  const normalized = normalizeProjectEditValue({ name, description });
  const canSave = Boolean(normalized.name) && !saving;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await onConfirm(project.id, normalized);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目修改失败，请稍后重试");
      setSaving(false);
    }
  };

  return (
    <div className="entry-dialog-layer">
      <button
        type="button"
        className="entry-dialog-scrim"
        aria-label="关闭编辑项目窗口"
        disabled={saving}
        onClick={onClose}
      />
      <section className="entry-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-project-title">
        <div className="entry-dialog-heading">
          <span>项目设置</span>
          <h2 id="edit-project-title">编辑复习项目</h2>
          <p>修改名称和描述，不会影响已上传资料或复习内容。</p>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="edit-project-name">
            项目名称 <em>必填</em> <small>{name.length} / {PROJECT_NAME_MAX_LENGTH}</small>
          </label>
          <input
            id="edit-project-name"
            autoFocus
            value={name}
            maxLength={PROJECT_NAME_MAX_LENGTH}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：微观经济学"
            disabled={saving}
            required
          />
          <label htmlFor="edit-project-description">
            项目描述 <small>可选 · {description.length} / {PROJECT_DESCRIPTION_MAX_LENGTH}</small>
          </label>
          <textarea
            id="edit-project-description"
            value={description}
            maxLength={PROJECT_DESCRIPTION_MAX_LENGTH}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="记录考试时间、复习范围或其他说明"
            disabled={saving}
          />
          {error && <p className="entry-dialog-error" role="alert">{error}</p>}
          <div className="entry-dialog-actions">
            <button type="button" disabled={saving} onClick={onClose}>取消</button>
            <button className="entry-primary" type="submit" disabled={!canSave}>
              {saving ? "正在保存…" : "保存修改"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
