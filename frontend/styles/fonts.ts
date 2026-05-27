import { JetBrains_Mono, Onest } from "next/font/google";

// Body / prose: Onest — a contemporary geometric grotesk with full Cyrillic,
// drawn 2023. Distinctive but highly legible for long answers and descriptions.
export const fontSans = Onest({
  subsets: ["latin", "cyrillic"],
  variable: "--font-sans",
  display: "swap",
});

// UI chrome, headings, labels, metrics and data: JetBrains Mono with Cyrillic.
// The monospace is the lead voice of the "analyst's terminal" aesthetic.
export const fontMono = JetBrains_Mono({
  subsets: ["latin", "cyrillic"],
  variable: "--font-mono",
  display: "swap",
});
