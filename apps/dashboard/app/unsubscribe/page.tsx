import Link from "next/link";
import { UnsubscribeForm } from "../_components/unsubscribe-form";

export const metadata = { title: "Email settings" };

/** Where a daily-check mail's unsubscribe link lands. No sign-in: the
 *  token in the link is the authority, and it only turns the mail off. */
export default async function UnsubscribePage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const token = (await searchParams).token ?? "";
  return (
    <div className="mx-auto max-w-md">
      <h1 className="text-[40px] leading-[1.05] font-extrabold tracking-[-0.02em] sm:text-[52px]">
        Stop these emails?
      </h1>
      <p className="mt-4 mb-10 text-ink-soft">Okwan&apos;s emails about new findings from the daily check.</p>
      {token ? (
        <UnsubscribeForm token={token} />
      ) : (
        <p className="text-ink-soft">
          This link is missing its token. You can turn the emails off in{" "}
          <Link href="/settings#daily-checks" className="underline">Settings</Link>.
        </p>
      )}
    </div>
  );
}
