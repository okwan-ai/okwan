"use client";

export function SignOut() {
  return (
    <button
      className="hover:text-ink"
      onClick={async () => {
        await fetch("/api/signout", { method: "POST" });
        window.location.href = "/signup?mode=signin";
      }}
    >
      Sign out
    </button>
  );
}
