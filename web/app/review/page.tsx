import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ReviewWorkspace } from "@/components/ReviewWorkspace";
import { serverAccount } from "@/lib/session";

export const metadata: Metadata = { title: "New review · reScrub" };

// /review opens the setup steps (sign-in required when a backend is configured);
// /review?demo=1 opens the public demonstration directly.
export default async function ReviewPage({ searchParams }: PageProps<"/review">) {
  const { demo } = await searchParams;
  const startInDemo = demo === "1";
  let account = null;
  if (!startInDemo && process.env.NEXT_PUBLIC_API_BASE_URL) {
    const found = await serverAccount();
    if (found === null) redirect("/login?next=%2Freview");
    account = found === "unchecked" ? null : found;
  }
  // The key makes switching between /review and /review?demo=1 start fresh.
  return <ReviewWorkspace key={startInDemo ? "demo" : "setup"} startInDemo={startInDemo} account={account} />;
}
