"use client";

import { useRouter } from "next/navigation";
import { IntakeScreen, type LanguageCode } from "@/components/IntakeScreen";
import type { Jurisdiction } from "@/lib/types";

export default function Home() {
  const router = useRouter();

  return (
    <IntakeScreen
      onStart={(
        jurisdiction: Jurisdiction,
        category: string | null,
        language: LanguageCode
      ) => {
        const params = new URLSearchParams({ jurisdiction, language });
        if (category) params.set("category", category);
        router.push(`/chat?${params.toString()}`);
      }}
    />
  );
}
