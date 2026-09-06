import { useEffect, useState } from "react";

/** Ticks up once a second while `active` is true, resets to 0 when it flips
 * back to false. Used to keep the loading state honest — per the API
 * contract, answers can legitimately take up to ~60s. */
export function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return;
    }
    const start = Date.now();
    const interval = setInterval(() => {
      setSeconds(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [active]);

  return seconds;
}
