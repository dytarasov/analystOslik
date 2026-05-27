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

// Set the theme class before first paint (reads the saved choice, else the OS
// preference) so there's no light→dark flash on load. Kept inline + tiny.
const THEME_INIT = `(function(){try{var t=localStorage.getItem('t2r:theme');if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}if(t==='dark'){document.documentElement.classList.add('dark');}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={cn(fontSans.variable, fontMono.variable)} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body className="min-h-screen bg-background font-sans antialiased">
        <TooltipProvider delayDuration={200} skipDelayDuration={300}>
          {children}
        </TooltipProvider>
        <Toaster richColors position="top-right" />
      </body>
    </html>
  );
}
