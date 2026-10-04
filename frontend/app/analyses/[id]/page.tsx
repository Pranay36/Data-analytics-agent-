import { AnalysisResult } from "@/features/analysis/AnalysisResult";

export default async function AnalysisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AnalysisResult id={id} />;
}
