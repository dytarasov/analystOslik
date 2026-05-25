"use client";

import { useEffect, useState } from "react";

import { ChatRunner } from "@/components/chat/ChatRunner";
import { api } from "@/lib/api";

export default function HomePage() {
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    api.client.listPublicSources().then(setSources).catch(() => setSources([]));
  }, []);

  return <ChatRunner sessionId={null} sources={sources} />;
}
