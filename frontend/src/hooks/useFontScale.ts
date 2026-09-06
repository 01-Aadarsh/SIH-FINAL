"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "ipsakti:fontScale";
const BASE_PX = 16;
const MIN_SCALE = 85;
const MAX_SCALE = 130;
const STEP = 10;

function readStoredScale(): number {
  if (typeof window === "undefined") return 100;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  const parsed = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(parsed) ? parsed : 100;
}

/** Site-wide text-size control: scales the root font-size so every Tailwind
 * rem-based class resizes proportionally, persisted across visits/pages. */
export function useFontScale() {
  const [scale, setScale] = useState(100);

  useEffect(() => {
    setScale(readStoredScale());
  }, []);

  useEffect(() => {
    document.documentElement.style.fontSize = `${(BASE_PX * scale) / 100}px`;
    try {
      window.localStorage.setItem(STORAGE_KEY, String(scale));
    } catch {
      // Storage unavailable (private mode, etc.) — scale still applies for this session.
    }
  }, [scale]);

  const increase = useCallback(() => setScale((s) => Math.min(MAX_SCALE, s + STEP)), []);
  const decrease = useCallback(() => setScale((s) => Math.max(MIN_SCALE, s - STEP)), []);
  const reset = useCallback(() => setScale(100), []);

  return { scale, increase, decrease, reset };
}
