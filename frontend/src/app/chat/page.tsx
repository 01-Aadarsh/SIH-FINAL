"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatView } from "@/components/ChatView";
import type { Jurisdiction } from "@/lib/types";

function isJurisdiction(value: string | null): value is Jurisdiction {
  return value === "india" || value === "international";
}

function ChatPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const jurisdictionParam = searchParams.get("jurisdiction");
  const category = searchParams.get("category") || null;

  useEffect(() => {
    if (!isJurisdiction(jurisdictionParam)) {
      router.replace("/");
    }
  }, [jurisdictionParam, router]);

  if (!isJurisdiction(jurisdictionParam)) {
    return null;
  }

  return (
    <ChatView
      jurisdiction={jurisdictionParam}
      category={category}
      onChangeContext={() => router.push("/")}
    />
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatPageContent />
    </Suspense>
  );
}
