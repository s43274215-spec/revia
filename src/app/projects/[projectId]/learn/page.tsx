import { LearningPage } from "@/components/learning/learning-page";

export default async function LearnPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <LearningPage projectId={projectId} />;
}
