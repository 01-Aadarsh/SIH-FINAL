"use client";

import type { Citation } from "@/lib/types";

export function CitationCard({
  citation,
  index,
  onView,
}: {
  citation: Citation;
  index: number;
  onView: (citation: Citation) => void;
}) {
  return (
    <div className="rounded-xl border border-clay-200 bg-white/70 p-3 text-sm">
      <div className="min-w-0">
        <p className="truncate font-medium text-ink">
          <span className="mr-1.5 text-ink/40">[{index + 1}]</span>
          {citation.source_file}
        </p>
        <p className="mt-0.5 text-xs text-ink/55">
          p. {citation.page_number} · {citation.section_heading}
        </p>
      </div>

      <button
        type="button"
        onClick={() => onView(citation)}
        className="mt-2 text-xs font-medium text-forest-600 underline decoration-forest-300 underline-offset-2 hover:text-forest-700"
      >
        View source PDF
      </button>
    </div>
  );
}
