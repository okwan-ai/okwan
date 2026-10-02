import "server-only";
import { redirect } from "next/navigation";
import { me, type Me } from "./api";

/** Pages behind sign-in. An expired session lands on sign-in, not an error. */
export async function requireTenant(): Promise<Me> {
  const tenant = await me();
  if (!tenant) redirect("/signup?mode=signin");
  return tenant;
}
