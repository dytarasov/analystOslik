import type { Metadata } from "next";
import { Toaster } from "sonner";

import { TooltipProvider } from "@/components/ui/tooltip";
import { fontMono, fontSans } from "@/styles/fonts";
import { cn } from "@/lib/utils";

import "./globals.css";

export const metadata: Metadata = {
  title: "Аналитический Ослик",
  description: "Спросите данные словами — Ослик принесёт отчёт",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={cn(fontSans.variable, fontMono.variable)}>
      <body className="min-h-screen bg-background font-sans antialiased">
        <TooltipProvider delayDuration={200} skipDelayDuration={300}>
          {children}
        </TooltipProvider>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
