import { requireTenant } from "@/lib/guard";
import { IssueKey } from "../_components/issue-key";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "API keys" };

export default async function KeyPage() {
  await requireTenant();
  return (
    <>
      <PageHeader
        title="API keys"
        description="Your workspace's own key. A key for a merchant reads only that merchant's rails; issue those from the merchant's API keys tab."
      />
      <IssueKey />
    </>
  );
}
