import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { LogoutButton } from "@/components/admin/LogoutButton";
import { DonkeyMark } from "@/components/shared/DonkeyMark";
import { ThemeToggle } from "@/components/shared/ThemeToggle";

export default function ProtectedAdminLayout({ children }: { children: React.ReactNode }) {
  const cookie = cookies().get("t2r_admin");
  if (!cookie) {
    redirect("/admin/login");
  }
  return (
    <div className="min-h-screen">
      <header className="border-b bg-card">
        <div className="container flex h-14 items-center justify-between">
          <Link href="/admin" className="flex items-center gap-2.5 font-mono text-sm font-semibold tracking-tight">
            <DonkeyMark size={28} rounded="rounded-md" />
            Аналитический Ослик
            <span className="label-mono rounded border border-dashed px-1.5 py-0.5">admin</span>
          </Link>
          <nav className="flex items-center gap-3 font-mono text-sm">
            <Link href="/admin" className="text-muted-foreground transition-colors hover:text-foreground">
              источники
            </Link>
            <ThemeToggle />
            <LogoutButton />
          </nav>
        </div>
      </header>
      <main className="container py-8">{children}</main>
    </div>
  );
}
