"use client";

import type { FormCard } from "@/lib/types";

/** Full-detail view of one compliance/form_navigator.py catalog match,
 * opened by clicking its ActionableFormCard. Same underlying data as the
 * card — this is a more spacious, dedicated view plus a prominent link
 * button, not different information. */
export function ActionableFormModal({
  form,
  onClose,
}: Readonly<{
  form: FormCard;
  onClose: () => void;
}>) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-ink/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={`form-modal-title-${form.form_id}`}
    >
      {/* A real <button> covering the backdrop, not a click handler on a
       * plain <div> — click-to-close-on-backdrop is a native button
       * interaction; keyboard/screen-reader users close via the explicit
       * ✕ button below instead (tabIndex={-1} keeps this out of tab
       * order so it doesn't sit awkwardly ahead of the real content). */}
      <button
        type="button"
        className="fixed inset-0 cursor-default"
        aria-label="Close"
        tabIndex={-1}
        onClick={onClose}
      />
      <div className="relative max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-saffron-600">
              {form.agency === "NBA" ? "National Biodiversity Authority" : "Indian Patent Office"}
            </p>
            <h2 id={`form-modal-title-${form.form_id}`} className="mt-1 text-lg font-semibold text-ink">
              {form.title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-full p-1.5 text-ink/40 hover:bg-clay-50 hover:text-ink/70"
          >
            ✕
          </button>
        </div>

        <dl className="mt-5 space-y-4 text-sm">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-ink/40">
              Statutory authority
            </dt>
            <dd className="mt-1 text-ink/80">{form.statutory_mandate}</dd>
          </div>

          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-ink/40">
              Filing deadline
            </dt>
            <dd className="mt-1 text-ink/80">{form.deadline}</dd>
          </div>

          {form.required_attachments.length > 0 && (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-ink/40">
                Mandatory attachments
              </dt>
              <dd className="mt-1">
                <ul className="list-inside list-disc space-y-1 text-ink/80">
                  {form.required_attachments.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
        </dl>

        <p className="mt-5 rounded-lg bg-clay-50 px-3 py-2 text-xs leading-relaxed text-ink/55">
          First-pass reference data — form numbers verified against the
          real indexed statute text where possible, but not a substitute
          for professional/legal review before actually filing.
        </p>

        <a
          href={form.submission_portal}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-5 block rounded-xl bg-forest-600 px-4 py-2.5 text-center text-sm font-semibold text-white transition hover:bg-forest-700"
        >
          Go to official submission portal →
        </a>
      </div>
    </div>
  );
}
