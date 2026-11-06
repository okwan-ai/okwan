import { AuthForm } from "../_components/auth-form";
import { SignedOutAside } from "../_components/signed-out-aside";

export async function generateMetadata({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  return { title: (await searchParams).mode === "signin" ? "Sign in" : "Create an account" };
}

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string; idle?: string; ended?: string }>;
}) {
  const params = await searchParams;
  const mode = params.mode === "signin" ? "signin" : "signup";
  const notice = mode !== "signin" ? null
    : params.idle === "1" ? "You were signed out after 30 minutes without activity, to keep your account safe. Sign in to continue."
      : params.ended === "1" ? "Your session ended: sign-ins last 12 hours at most. Sign in to continue."
        : null;
  return (
    <div className="grid items-center gap-12 lg:grid-cols-[minmax(0,605fr)_minmax(0,733fr)] lg:gap-[56px] 2xl:gap-[67px]">
      <div className="w-full max-w-[605px]">
        <h1 className="pl-2.5 text-[40px] leading-[1.05] font-extrabold tracking-[-0.02em] sm:text-[54px] 2xl:text-[66px]">
          {mode === "signup" ? "Find where your systems disagree." : "Welcome back."}
        </h1>
        <p className="mt-4 mb-11 pl-3 text-[17px] text-ink-soft 2xl:text-[23px]">
          {mode === "signup"
            ? "One account, one workspace. Connect a system, prove it with a live read, and take an API key for your agents."
            : "Sign in to manage connections and keys."}
        </p>
        {notice && (
          <p role="status" className="mb-6 rounded-[18px] border border-white/90 bg-white/80 px-5 py-4 text-[15px] text-ink-soft shadow-float">{notice}</p>
        )}
        <AuthForm mode={mode} />
      </div>
      <SignedOutAside />
    </div>
  );
}
