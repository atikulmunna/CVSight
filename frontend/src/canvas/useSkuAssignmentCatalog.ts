import { useEffect, useMemo, useRef, useState } from "react";

import { loadCatalog } from "../catalog/api";
import { DEMO_SKUS } from "../catalog/demoCatalog";
import { searchSkus, type Sku } from "../catalog/model";

const RECENTS_KEY = "shelfsight:sku-recents";
const MAX_RECENTS = 12;

export function useSkuAssignmentCatalog(enabled: boolean) {
  const [skus, setSkus] = useState<Sku[]>(DEMO_SKUS);
  const [query, setQuery] = useState("");
  const [recents, setRecents] = useState(() => storedIds());
  const [source, setSource] = useState<"loading" | "live" | "fixture">(
    enabled ? "loading" : "fixture",
  );
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const controller = new AbortController();
    void loadCatalog((input, init) =>
      fetch(input, { ...init, signal: controller.signal }),
    )
      .then((loaded) => {
        setSkus(loaded);
        setSource("live");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setSkus(DEMO_SKUS);
        setSource("fixture");
      });
    return () => controller.abort();
  }, [enabled]);

  const activeSkus = useMemo(
    () => skus.filter((sku) => sku.status === "active"),
    [skus],
  );
  const candidates = useMemo(
    () =>
      searchSkus(
        activeSkus.filter((sku) => !sku.isUnknown),
        query,
      ).slice(0, 9),
    [activeSkus, query],
  );
  const recentSkus = useMemo(() => {
    const byId = new Map(activeSkus.map((sku) => [sku.id, sku]));
    return recents
      .map((id) => byId.get(id))
      .filter((sku): sku is Sku => sku !== undefined && !sku.isUnknown);
  }, [activeSkus, recents]);

  function remember(sku: Sku) {
    if (sku.isUnknown) {
      return;
    }
    const next = [sku.id, ...recents.filter((id) => id !== sku.id)].slice(
      0,
      MAX_RECENTS,
    );
    setRecents(next);
    try {
      localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
    } catch {
      return;
    }
  }

  return {
    candidates,
    query,
    setQuery,
    recentSkus,
    unknownSku: activeSkus.find((sku) => sku.isUnknown) ?? null,
    source,
    searchInputRef,
    remember,
  };
}

function storedIds(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENTS_KEY) ?? "[]");
    return Array.isArray(value)
      ? value.filter((id): id is string => typeof id === "string").slice(0, MAX_RECENTS)
      : [];
  } catch {
    return [];
  }
}
