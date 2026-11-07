import { ImportFlow } from "../../../_components/import-flow";
import { Section } from "../../../_components/ui/page-header";
import { savedLayouts } from "@/lib/merchant-files";

export const metadata = { title: "Import a file" };

/**
 * Import a file for one merchant, under its Files tab. The merchant layout
 * has already checked access; the saved mappings come from the API, and
 * the flow itself runs in the browser (the file is read there and sent to
 * the API, never kept by the dashboard).
 */
export default async function ImportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const layouts = await savedLayouts(id);
  return (
    <Section
      className="mt-0"
      title="Import a file"
      description="A processor's settlement report or transaction export, a delivery platform's report, or a bank statement, mapped column by column."
    >
      <ImportFlow tenantId={id} layouts={layouts.ok ? layouts.data.layouts : []} />
    </Section>
  );
}
