"use client";

import { DragEvent, useRef, useState } from "react";

export type SelectedPDF = {
  id: string;
  name: string;
  size: number;
  kind: "course_material" | "syllabus";
  file: File;
};

export function FileDropZone({ title, hint, kind, multiple = false, files, onFiles }: { title: string; hint: string; kind: SelectedPDF["kind"]; multiple?: boolean; files: SelectedPDF[]; onFiles: (files: SelectedPDF[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const accept = (selected: FileList | File[]) => {
    const pdfs = Array.from(selected).filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    const mapped = pdfs.map((file, index) => ({ id: `${kind}-${Date.now()}-${index}`, name: file.name, size: file.size, kind, file }));
    onFiles(multiple ? [...files, ...mapped] : mapped.slice(0, 1));
  };
  const drop = (event: DragEvent) => { event.preventDefault(); setDragging(false); accept(event.dataTransfer.files); };

  return (
    <div className={`file-drop-zone ${dragging ? "is-dragging" : ""}`} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={drop}>
      <input ref={inputRef} type="file" accept="application/pdf,.pdf" multiple={multiple} onChange={(event) => event.target.files && accept(event.target.files)} />
      <span className="file-symbol">↑</span>
      <strong>{title}</strong>
      <p>{hint}</p>
      <button type="button" onClick={() => inputRef.current?.click()}>选择 PDF</button>
      {files.length > 0 && <div className="selected-files">
        {files.map((file) => <div key={file.id}><span>PDF</span><p><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB</small></p></div>)}
      </div>}
    </div>
  );
}
