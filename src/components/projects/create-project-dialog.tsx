"use client";

import { FormEvent, useState } from "react";

export function CreateProjectDialog({ open, onClose, onConfirm }: { open: boolean; onClose: () => void; onConfirm: (value: { name: string; description: string }) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  if (!open) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    onConfirm({ name: name.trim(), description: description.trim() });
  };

  return (
    <div className="entry-dialog-layer">
      <button className="entry-dialog-scrim" aria-label="关闭新建项目窗口" onClick={onClose} />
      <section className="entry-dialog" role="dialog" aria-modal="true" aria-labelledby="create-project-title">
        <div className="entry-dialog-heading"><span>新项目</span><h2 id="create-project-title">创建复习项目</h2><p>为一门课程建立独立的复习空间。</p></div>
        <form onSubmit={submit}>
          <label htmlFor="course-name">课程名称 <em>必填</em></label>
          <input id="course-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：微观经济学" required />
          <label htmlFor="course-description">课程描述 <small>可选</small></label>
          <textarea id="course-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="记录考试时间、复习范围或其他说明" />
          <div className="entry-dialog-actions"><button type="button" onClick={onClose}>取消</button><button className="entry-primary" type="submit" disabled={!name.trim()}>确认创建</button></div>
        </form>
      </section>
    </div>
  );
}
