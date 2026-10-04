import { PageHeader } from "@/components/ui/primitives";
import { SchemaBrowser } from "@/features/datasources/SchemaBrowser";

export default async function DatasourcePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <PageHeader title="Schema" subtitle="What the analyst can see in this data source." />
      <SchemaBrowser id={id} />
    </>
  );
}
