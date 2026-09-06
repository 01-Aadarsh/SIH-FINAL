"use client";

import type { Citation } from "@/lib/types";
import { sourceUrl } from "@/lib/api";

export function SourceViewer({
  citation,
  onClose,
}: {
  citation: Citation | null;
  onClose: () => void;
}) {
  if (!citation) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-ink/40">
        <p className="text-sm font-medium">No source open</p>
        <p className="max-w-xs text-xs">
          Click &quot;View source PDF&quot; on any citation to open the exact
          page it was drawn from, side by side with the conversation.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-clay-200 bg-white px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">
            {citation.source_file}
          </p>
          <p className="text-xs text-ink/50">
            Page {citation.page_number} · {citation.section_heading}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded-full p-1.5 text-ink/40 hover:bg-clay-50 hover:text-ink/70"
          aria-label="Close source viewer"
        >
          ✕
        </button>
      </div>
      <iframe
        key={citation.chunk_id}
        title={`${citation.source_file}, page ${citation.page_number}`}
        src={sourceUrl(citation.source_file, citation.page_number)}
        className="h-full w-full flex-1 bg-clay-50"
      />
    </div>
  );
}
