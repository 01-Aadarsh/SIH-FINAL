"use client";

import type { RelatedProvision } from "@/lib/types";

/** graph_kg/kg.py's knowledge-graph cross-references — a pointer at a real,
 * separately-indexed chunk, never new generated text. `cross_jurisdiction_
 * counterpart` deliberately crosses the jurisdiction switch (e.g. a
 * domestic Section 3(p) question surfacing the WIPO GRATK Treaty's
 * disclosure obligation as a *pointer*, while the answer/citations above
 * stay scoped to the selected jurisdiction). */
export function RelatedProvisionCard({ provision }: { provision: RelatedProvision }) {
  const relationLabel =
    provision.relation === "cross_jurisdiction_counterpart"
      ? "International counterpart"
      : "Related provision";

  return (
    <div className="rounded-xl border border-clay-200 bg-white/70 p-3 text-sm">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-forest-600">
        {relationLabel}
      </p>
      <p className="mt-1 truncate font-medium text-ink">{provision.source_file}</p>
      <p className="mt-0.5 text-xs text-ink/55">
        p. {provision.page_number} · {provision.section_heading}
      </p>
    </div>
  );
}
