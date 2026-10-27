import { useEffect, useState } from "react";

import { loadSamStatus, type SamStatus } from "./smartBoxApi";

// The tunnel to SAM can drop while the app stays open, so the status is checked again
// every minute.
const CHECK_INTERVAL_MS = 60_000;

// Whether the SAM service answers, or null while unknown, for example while the API is
// unavailable.
export function useSamStatus(): SamStatus | null {
  const [status, setStatus] = useState<SamStatus | null>(null);

  useEffect(() => {
    let active = true;
    async function check() {
      const next = await loadSamStatus();
      if (active) {
        setStatus(next);
      }
    }
    void check();
    const timer = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return status;
}
