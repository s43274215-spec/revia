"use client";

import { useParams } from "next/navigation";
import { ProjectUploadPage } from "@/components/upload/project-upload-page";

export default function UploadPage() {
  const { projectId } = useParams<{ projectId: string }>();
  return <ProjectUploadPage projectId={projectId} />;
}
