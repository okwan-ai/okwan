import { notFound } from "next/navigation";
import { railLabel } from "@/lib/finding";
import { requireTenant } from "@/lib/guard";
import { safeBack } from "@/lib/oauth-connect";
import { ButtonLink } from "../../../_components/ui/button";
import { Card, CardBody, CardHeader } from "../../../_components/ui/card";
import { PageHeader } from "../../../_components/ui/page-header";

export const metadata = { title: "Connect" };

const KNOWN = new Set(["gusto"]);

/** Where a Connect button lands: connected and what the test read, or why not. */
export default async function ConnectDone({ params, searchParams }: {
  params: Promise<{ name: string }>;
  searchParams: Promise<{ result?: string; reason?: string; test?: string; rows?: string; detail?: string; back?: string }>;
}) {
  await requireTenant();
  const [{ name: connector }, q] = await Promise.all([params, searchParams]);
  if (!KNOWN.has(connector)) notFound();
  const label = railLabel(connector);
  const back = safeBack(q.back);
  const connected = q.result === "connected";
  const title = connected ? `${label} is connected` : q.result === "declined" ? `${label} wasn't connected` : `${label} couldn't be connected`;

  return (
    <>
      <PageHeader title={title} />
      <Card>
        <CardHeader title={connected ? "Test read" : "What happened"} />
        <CardBody>
          <p className="text-sm">
            {connected
              ? q.test === "rows"
                ? `Okwan read ${q.rows || "some"} ${q.rows === "1" ? "payroll" : "payrolls"} from ${label} as its test. It reads only; it can't run payroll or change anything.`
                : q.detail || `The connection works. ${label} returned no payrolls in the window Okwan reads by default.`
              : q.result === "declined"
                ? `Access wasn't approved in ${label}, so nothing was connected.`
                : q.reason || "Something went wrong. Connect again from the same browser."}
          </p>
          <div className="mt-4">
            <ButtonLink href={back} variant="primary">Back</ButtonLink>
          </div>
        </CardBody>
      </Card>
    </>
  );
}
