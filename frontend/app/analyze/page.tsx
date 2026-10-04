import { Suspense } from "react";
import { PageHeader } from "@/components/ui/primitives";
import { AskForm } from "@/features/analysis/AskForm";

export default function AnalyzePage() {
  return (
    <>
      <PageHeader title="Ask a question" subtitle="Plain English in, a dashboard with the evidence out." />
      <Suspense>
        <AskForm />
      </Suspense>
    </>
  );
}
