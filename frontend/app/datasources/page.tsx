import { PageHeader } from "@/components/ui/primitives";
import { DatasourceManager } from "@/features/datasources/DatasourceManager";

export default function DatasourcesPage() {
  return (
    <>
      <PageHeader title="Data sources" subtitle="Where the questions get answered." />
      <DatasourceManager />
    </>
  );
}
