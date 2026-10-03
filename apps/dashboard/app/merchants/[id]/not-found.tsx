import Link from "next/link";

export default function MerchantNotFound() {
  return (
    <div className="card p-8">
      <p className="font-display text-2xl">No such merchant.</p>
      <p className="mt-3 max-w-xl text-ink-soft">
        The Okwan API has no merchant with that id under your account.
      </p>
      <Link href="/merchants" className="btn btn-secondary mt-6">Back to merchants</Link>
    </div>
  );
}
