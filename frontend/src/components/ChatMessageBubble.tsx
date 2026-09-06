import type { Citation, ConversationMessage } from "@/lib/types";
import { CitationCard } from "./CitationCard";
import { AbstentionBanner } from "./AbstentionBanner";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { RelatedProvisionCard } from "./RelatedProvisionCard";
import { ActionableFormCard } from "./ActionableFormCard";
import { ComplianceFlags } from "./ComplianceFlags";

export function ChatMessageBubble({
  message,
  onViewCitation,
}: Readonly<{
  message: ConversationMessage;
  onViewCitation: (citation: Citation) => void;
}>) {
  const isUser = message.role === "user";

  if (message.error) {
    return (
      <div className="max-w-[85%] animate-fadeIn rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow-panel">
        <p className="font-medium">The backend couldn&apos;t answer that.</p>
        <p className="mt-1 text-red-600/90">{message.error}</p>
      </div>
    );
  }

  const abstained = !!message.flags?.abstained;

  return (
    <div
      className={`max-w-[85%] animate-fadeIn rounded-2xl px-4 py-3 shadow-panel ${
        isUser
          ? "ml-auto border border-forest-600 bg-forest-600 text-white"
          : "border border-clay-200 bg-white text-ink"
      }`}
    >
      <p className="whitespace-pre-wrap text-sm leading-relaxed">
        {message.content}
      </p>

      {!isUser && !abstained && typeof message.confidence_score === "number" && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <ConfidenceBadge confidence={Math.round(message.confidence_score * 100)} />
          {message.formulation_category && (
            <span className="rounded-full border border-clay-200 bg-clay-50 px-2 py-0.5 text-xs font-medium text-ink/60">
              {message.formulation_category}
            </span>
          )}
        </div>
      )}

      {!isUser && !abstained && message.flags?.weak_grounding && (
        <p className="mt-2 rounded-lg bg-saffron-100/60 px-2.5 py-1.5 text-xs text-saffron-600">
          The keyword search found a strong lexical match, but the reranker
          wasn&apos;t confident it actually answers this — worth double-checking
          the sources below rather than treating this as fully settled.
        </p>
      )}

      {!isUser && abstained && <AbstentionBanner />}

      {!isUser && message.needs_clarification && message.clarifying_questions?.[0] && (
        <p className="mt-2 rounded-lg bg-clay-50 px-2.5 py-1.5 text-xs text-ink/70">
          {message.clarifying_questions[0]}
        </p>
      )}

      {!isUser &&
        !abstained &&
        message.citations &&
        message.citations.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-clay-200 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/40">
              Sources
            </p>
            {message.citations.map((c, i) => (
              <CitationCard
                key={c.chunk_id}
                citation={c}
                index={i}
                onView={onViewCitation}
              />
            ))}
          </div>
        )}

      {!isUser &&
        !abstained &&
        message.related_provisions &&
        message.related_provisions.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-clay-200 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/40">
              See also
            </p>
            {message.related_provisions.map((p, i) => (
              <RelatedProvisionCard key={`${p.tag}-${i}`} provision={p} />
            ))}
          </div>
        )}

      {!isUser &&
        !abstained &&
        message.actionable_forms &&
        message.actionable_forms.length > 0 && (
          <div className="mt-3 space-y-2 border-t border-clay-200 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink/40">
              Forms you may need
            </p>
            {message.actionable_forms.map((f) => (
              <ActionableFormCard key={f.form_id} form={f} />
            ))}
          </div>
        )}

      {!isUser && !abstained && (
        <ComplianceFlags flags={message.compliance_flags ?? []} />
      )}
    </div>
  );
}
