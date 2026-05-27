"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ChatRunner,
  type ActiveTask,
  type RestoredMessage,
} from "@/components/chat/ChatRunner";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

function ChatSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[57px] shrink-0 items-center justify-end border-b px-6">
        <Skeleton className="h-8 w-40 rounded-lg" />
      </div>
      <div className="flex-1 px-4 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          <div className="flex justify-end">
            <Skeleton className="h-10 w-2/5 rounded-2xl" />
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-7 w-7 shrink-0 rounded-full" />
            <Skeleton className="h-24 w-3/4 rounded-2xl" />
          </div>
          <div className="flex justify-end">
            <Skeleton className="h-10 w-1/3 rounded-2xl" />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SessionPage() {
  const params = useParams<{ sessionId: string }>();
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);
  const [messages, setMessages] = useState<RestoredMessage[] | null>(null);
  const [active, setActive] = useState<ActiveTask | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.client.listPublicSources().then((s) => {
      if (!cancelled) setSources(s);
    }).catch(() => undefined);

    Promise.all([
      api.client.listMessages(params.sessionId),
      api.client.activeTask(params.sessionId),
    ])
      .then(([m, a]) => {
        if (cancelled) return;
        setMessages(
          m.map((x) => ({
            role: x.role,
            content: x.content,
            metadata: x.metadata || {},
          })),
        );
        setActive(a);
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      });

    return () => {
      cancelled = true;
    };
  }, [params.sessionId]);

  if (messages === null) {
    return <ChatSkeleton />;
  }
  return (
    <ChatRunner
      sessionId={params.sessionId}
      sources={sources}
      initialMessages={messages}
      activeTask={active}
    />
  );
}
