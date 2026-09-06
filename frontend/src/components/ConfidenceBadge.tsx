function toneFor(confidence: number): string {
  if (confidence >= 75) return "bg-forest-50 text-forest-700 border-forest-100";
  if (confidence >= 45) return "bg-saffron-100 text-saffron-600 border-saffron-300/60";
  return "bg-clay-50 text-ink/60 border-clay-200";
}

export function ConfidenceBadge({ confidence }: Readonly<{ confidence: number }>) {
  const tone = toneFor(confidence);

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium tabular-nums ${tone}`}
      title="The cross-encoder reranker's confidence in the strongest retrieved passage — reflects retrieval quality, not a guarantee the final answer is correct."
    >
      {confidence}% match
    </span>
  );
}
