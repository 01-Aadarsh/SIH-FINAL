"use client";

import { useEffect, useState } from "react";
import { health } from "@/lib/api";
import type { Jurisdiction } from "@/lib/types";

export function Header({
  jurisdiction,
  category,
  onChangeContext,
}: {
  jurisdiction: Jurisdiction;
  category: string | null;
  onChangeContext: () => void;
}) {
  const [backendUp, setBackendUp] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => health().then((ok) => !cancelled && setBackendUp(ok));
    check();
    const interval = setInterval(check, 20_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <header className="flex items-center justify-between gap-3 border-b border-clay-200 bg-white px-4 py-3 sm:px-6">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onChangeContext}
          className="flex shrink-0 items-center gap-1.5 rounded-full border border-clay-200 px-3 py-1.5 text-xs font-semibold text-ink/70 transition hover:border-forest-300 hover:bg-forest-50 hover:text-forest-700"
        >
          <span aria-hidden>←</span> Back to setup
        </button>

        <div className="min-w-0 border-l border-clay-200 pl-3">
          <p className="truncate font-serif text-base font-semibold text-ink">
            IP-SAKTI Sahayak
          </p>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink/50">
            <span className="rounded-full bg-forest-50 px-2 py-0.5 font-medium text-forest-700">
              {jurisdiction === "india" ? "India" : "International"}
            </span>
            {category && (
              <span className="rounded-full bg-saffron-100 px-2 py-0.5 font-medium text-saffron-600">
                {category}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-1.5 text-xs text-ink/50">
        <span
          className={`h-2 w-2 rounded-full ${
            backendUp === null
              ? "bg-clay-200"
              : backendUp
                ? "bg-forest-500"
                : "bg-red-400"
          }`}
        />
        {backendUp === null
          ? "Checking backend..."
          : backendUp
            ? "Backend online"
            : "Backend unreachable"}
      </div>
    </header>
  );
}
