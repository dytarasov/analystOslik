"use client";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

export function LogoutButton() {
  async function onClick() {
    try {
      await api.auth.logout();
    } catch {
      // ignore
    }
    window.location.href = "/admin/login";
  }
  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick}>
      Выйти
    </Button>
  );
}
