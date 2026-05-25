"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  ChatRunner,
  type ActiveTask,
  type RestoredMessage,
} from "@/components/chat/ChatRunner";
import { api } from "@/lib/api";

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
    return <div className="p-6 text-sm text-muted-foreground">Загрузка…</div>;
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
