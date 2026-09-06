"use client";

import type { ComplianceFlag } from "@/lib/types";

/** generation/compliance_flags.py's output — short, generic pointers at
 * compliance checkpoints the citations above already cover. Deliberately
 * plain styling (no red/amber "warning" treatment): these aren't risk
 * scores or alerts, just a nudge toward re-reading a specific citation. */
export function ComplianceFlags({ flags }: Readonly<{ flags: ComplianceFlag[] }>) {
  if (!flags || flags.length === 0) return null;

  return (
    <div className="mt-3 space-y-1.5 border-t border-clay-200 pt-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink/40">
        Worth a closer look
      </p>
      {flags.map((f) => (
        <p key={f.tag} className="rounded-lg bg-clay-50 px-2.5 py-1.5 text-xs text-ink/70">
          {f.note}
        </p>
      ))}
    </div>
  );
}
