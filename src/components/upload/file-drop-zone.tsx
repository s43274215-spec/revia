"use client";

import { DragEvent, useRef, useState } from "react";

export type SelectedPDF = {
  id: string;
  name: string;
  size: number;
  kind: "course_material" | "syllabus";
  file: File;
};

export function FileDropZone({ title, hint, kind, multiple = false, files, onFiles, removable = false }: { title: string; hint: string; kind: SelectedPDF["kind"]; multiple?: boolean; files: SelectedPDF[]; onFiles: (files: SelectedPDF[]) => void; removable?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const accept = (selected: FileList | File[]) => {
    const candidates = Array.from(selected);
    const invalidType = candidates.find((file) => file.type !== "application/pdf" || !file.name.toLowerCase().endsWith(".pdf"));
    if (invalidType) {
      setValidationError("仅支持 MIME 类型为 application/pdf 的 .pdf 文件");
      return;
    }
    const oversized = candidates.find((file) => file.size > 150 * 1024 * 1024);
    if (oversized) {
      setValidationError("PDF 文件不能超过 150MB");
      return;
    }
    setValidationError(null);
    const pdfs = candidates;
    const mapped = pdfs.map((file, index) => ({ id: `${kind}-${Date.now()}-${index}`, name: file.name, size: file.size, kind, file }));
    onFiles(multiple ? [...files, ...mapped] : mapped.slice(0, 1));
  };
  const drop = (event: DragEvent) => { event.preventDefault(); setDragging(false); accept(event.dataTransfer.files); };
  const removeFile = (fileId: string) => {
    if (!removable) return;
    onFiles(files.filter((file) => file.id !== fileId));
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className={`file-drop-zone ${dragging ? "is-dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <input ref={inputRef} type="file" accept="application/pdf,.pdf" multiple={multiple} onChange={(event) => event.target.files && accept(event.target.files)} />
      <span className="file-symbol">↑</span>
      <strong>{title}</strong>
      <p>{hint}</p>
      <button type="button" onClick={() => inputRef.current?.click()}>选择 PDF</button>
      {validationError && <p className="file-validation-error" role="alert">{validationError}</p>}
      {files.length > 0 && <div className="selected-files">
        {files.map((file) => <div key={file.id}><span>PDF</span><p><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB</small></p>{removable && <button type="button" className="selected-file-remove" aria-label={`删除 ${file.name}`} title={`删除 ${file.name}`} onClick={() => removeFile(file.id)}><svg aria-hidden="true" viewBox="0 0 24 24" fill="none"><path d="M4 7h16M9 7V4h6v3m-8 0 1 13h8l1-13M10 11v5m4-5v5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></button>}</div>)}
      </div>}
    </div>
  );
}
