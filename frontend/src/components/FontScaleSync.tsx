"use client";

import { useFontScale } from "@/hooks/useFontScale";

/** Applies the persisted site-wide text-size preference on every page mount.
 * Renders nothing — mount this once near the root so pages that don't
 * render the A-/A/A+ control still respect a preference set elsewhere. */
export function FontScaleSync() {
  useFontScale();
  return null;
}
