import { AuthForm } from "../_components/auth-form";

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
    <div className="mx-auto max-w-md">
      <h1 className="font-display text-5xl font-light leading-[1.05] tracking-tight">
        {mode === "signup" ? "Reconcile every rail you collect on." : "Welcome back."}
      </h1>
      <p className="mt-4 mb-10 text-ink-soft">
        {mode === "signup"
          ? "One account, one workspace. Connect a rail, prove it with a live read, and take an API key for your agents."
          : "Sign in to manage connections and keys."}
      </p>
      <AuthForm mode={mode} />
    </div>
  );
}
