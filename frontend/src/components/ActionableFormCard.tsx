"use client";

import { useState } from "react";
import type { FormCard } from "@/lib/types";
import { ActionableFormModal } from "./ActionableFormModal";

/** compliance/form_navigator.py's catalog match — first-pass reference
 * data (form numbers verified against the real indexed statute text where
 * possible), not a substitute for professional/legal review before
 * actually filing. Clicking anywhere on the card opens the full-detail
 * modal (ActionableFormModal); the portal link also works directly from
 * here without opening it, via stopPropagation. */
export function ActionableFormCard({ form }: { form: FormCard }) {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setModalOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setModalOpen(true);
          }
        }}
        className="cursor-pointer rounded-xl border border-saffron-300/60 bg-saffron-100/40 p-3 text-sm transition hover:border-saffron-400 hover:bg-saffron-100/70"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-saffron-600">
              {form.agency}
            </p>
            <p className="mt-0.5 font-medium text-ink">{form.title}</p>
          </div>
        </div>
        <p className="mt-1.5 text-xs text-ink/60">{form.statutory_mandate}</p>
        <p className="mt-1 text-xs text-ink/60">
          <span className="font-medium text-ink/70">Deadline:</span> {form.deadline}
        </p>
        {form.required_attachments.length > 0 && (
          <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-xs text-ink/55">
            {form.required_attachments.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        <a
          href={form.submission_portal}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="mt-2 inline-block text-xs font-medium text-forest-600 underline decoration-forest-300 underline-offset-2 hover:text-forest-700"
        >
          Go to submission portal →
        </a>
      </div>

      {modalOpen && (
        <ActionableFormModal form={form} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}
