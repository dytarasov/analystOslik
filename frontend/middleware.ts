import { NextResponse, type NextRequest } from "next/server";

import { ACCESS_REQUIRED } from "@/lib/env";

const ADMIN_PUBLIC_PATHS = ["/admin/login"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // --- Админка: логин/пароль (cookie t2r_admin) ---
  if (pathname.startsWith("/admin")) {
    if (ADMIN_PUBLIC_PATHS.includes(pathname)) return NextResponse.next();
    if (!req.cookies.get("t2r_admin")) {
      const url = req.nextUrl.clone();
      url.pathname = "/admin/login";
      url.searchParams.set("next", pathname);
      return NextResponse.redirect(url);
    }
    return NextResponse.next();
  }

  // --- Клиентская часть: UUID-ключ (cookie t2r_access), если включено ---
  if (ACCESS_REQUIRED && pathname !== "/unlock") {
    if (!req.cookies.get("t2r_access")) {
      const url = req.nextUrl.clone();
      url.pathname = "/unlock";
      url.searchParams.set("next", pathname + req.nextUrl.search);
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  // Гейтим все страницы, кроме статики и внутренних ассетов Next.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.png|.*\\.\\w+$).*)"],
};
