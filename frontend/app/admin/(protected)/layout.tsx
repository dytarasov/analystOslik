import Link from "next/link";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { LogoutButton } from "@/components/admin/LogoutButton";
import { DonkeyMark } from "@/components/shared/DonkeyMark";

export default function ProtectedAdminLayout({ children }: { children: React.ReactNode }) {
  const cookie = cookies().get("t2r_admin");
  if (!cookie) {
    redirect("/admin/login");
  }
  return (
    <div className="min-h-screen">
      <header className="border-b bg-card">
        <div className="container flex h-14 items-center justify-between">
          <Link href="/admin" className="flex items-center gap-2 font-semibold tracking-tight">
            <DonkeyMark size={28} />
            Аналитический Ослик <span className="text-muted-foreground">· админка</span>
          </Link>
          <nav className="flex items-center gap-3 text-sm">
            <Link href="/admin" className="text-muted-foreground hover:text-foreground">
              Источники
            </Link>
            <LogoutButton />
          </nav>
        </div>
      </header>
      <main className="container py-8">{children}</main>
    </div>
  );
}
