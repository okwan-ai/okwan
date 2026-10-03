import { redirect } from "next/navigation";
import { me } from "@/lib/api";

export default async function Home() {
  redirect((await me()) ? "/overview" : "/signup");
}
