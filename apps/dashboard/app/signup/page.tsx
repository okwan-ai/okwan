import { AuthForm } from "../_components/auth-form";
import { SignedOutAside } from "../_components/signed-out-aside";

export async function generateMetadata({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  return { title: (await searchParams).mode === "signin" ? "Sign in" : "Create an account" };
}

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  const mode = (await searchParams).mode === "signin" ? "signin" : "signup";
  return (
    <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-16">
      <div className="mx-auto w-full max-w-md lg:mx-0">
        <h1 className="text-[44px] leading-[1.02] font-semibold tracking-[-0.04em] sm:text-5xl">
          {mode === "signup" ? "Find where your systems disagree." : "Welcome back."}
        </h1>
        <p className="mt-4 mb-10 text-ink-soft">
          {mode === "signup"
            ? "One account, one workspace. Connect a system, prove it with a live read, and take an API key for your agents."
            : "Sign in to manage connections and keys."}
        </p>
        <AuthForm mode={mode} />
      </div>
      <SignedOutAside />
    </div>
  );
}
