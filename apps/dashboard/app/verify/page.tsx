import Link from "next/link";
import { VerifyForm } from "../_components/verify-form";

export const metadata = { title: "Confirm your email" };

export default async function VerifyPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string }>;
}) {
  const token = (await searchParams).token ?? "";
  return (
    <div className="mx-auto max-w-md">
      <h1 className="text-[44px] leading-[1.02] font-semibold tracking-[-0.04em] sm:text-5xl">
        Confirm your email.
      </h1>
      <p className="mt-4 mb-10 text-ink-soft">
        This creates your workspace. Enter your password to finish.
      </p>
      {token ? (
        <VerifyForm token={token} />
      ) : (
        <p className="text-ink-soft">
          This link is missing its token. <Link href="/signup" className="underline">Sign up again</Link>.
        </p>
      )}
    </div>
  );
}
