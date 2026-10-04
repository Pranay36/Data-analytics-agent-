import { PageHeader } from "@/components/ui/primitives";
import { HistoryList } from "@/features/history/HistoryList";

export default function HistoryPage() {
  return (
    <>
      <PageHeader title="History" subtitle="Every analysis is saved, with the queries behind it." />
      <HistoryList />
    </>
  );
}
