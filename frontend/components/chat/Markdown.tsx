"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

type El = { children?: ReactNode };

const components: Components = {
  p: ({ children }: El) => <p className="leading-relaxed">{children}</p>,
  ul: ({ children }: El) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }: El) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }: El) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }: El) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }: El) => <em className="italic">{children}</em>,
  h1: ({ children }: El) => <h3 className="mt-1 text-base font-semibold">{children}</h3>,
  h2: ({ children }: El) => <h3 className="mt-1 text-base font-semibold">{children}</h3>,
  h3: ({ children }: El) => <h4 className="mt-1 text-sm font-semibold">{children}</h4>,
  a: ({ children, href }: El & { href?: string }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-primary underline underline-offset-2"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }: El) => (
    <blockquote className="border-l-2 border-border pl-3 text-muted-foreground">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-border" />,
  pre: ({ children }: El) => (
    <pre className="scroll-thin overflow-x-auto rounded-md bg-muted p-3 text-xs">
      {children}
    </pre>
  ),
  code: ({ className, children }: El & { className?: string }) => {
    if ((className || "").includes("language-")) {
      return <code className={cn("font-mono", className)}>{children}</code>;
    }
    return (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">
        {children}
      </code>
    );
  },
  table: ({ children }: El) => (
    <div className="scroll-thin overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  thead: ({ children }: El) => <thead className="bg-muted/50">{children}</thead>,
  th: ({ children }: El) => (
    <th className="border border-border px-2 py-1 text-left font-medium">{children}</th>
  ),
  td: ({ children }: El) => <td className="border border-border px-2 py-1">{children}</td>,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&>*:first-child]:mt-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
