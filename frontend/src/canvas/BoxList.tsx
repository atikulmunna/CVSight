import { useEffect, useMemo, useRef, useState } from "react";

import type { AnnotationBox } from "./model";
import { skuLabel } from "./model";

const ROW_HEIGHT = 50;
const OVERSCAN = 5;

// The boxes in spatial reading order. Only the rows in view are rendered, so a dense
// shelf keeps the list cheap.
export function BoxList({
  title,
  boxes,
  totalCount,
  selectedIds,
  focusedId,
  onSelect,
}: {
  title: string;
  boxes: AnnotationBox[];
  totalCount: number;
  selectedIds: ReadonlySet<string>;
  // The box the list keeps scrolled into view.
  focusedId: string | null;
  onSelect: (id: string, extend: boolean) => void;
}) {
  const [search, setSearch] = useState("");
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(900);
  const listRef = useRef<HTMLDivElement>(null);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return boxes;
    }
    return boxes.filter((box) =>
      `${box.id} ${box.sku} ${box.state} ${box.classType}`.toLowerCase().includes(query),
    );
  }, [boxes, search]);

  useEffect(() => {
    const measure = () => setViewportHeight(listRef.current?.clientHeight || 900);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  useEffect(() => {
    const index = filtered.findIndex((box) => box.id === focusedId);
    const list = listRef.current;
    if (index < 0 || !list) {
      return;
    }
    const rowTop = index * ROW_HEIGHT;
    const rowBottom = rowTop + ROW_HEIGHT;
    if (rowTop < list.scrollTop) {
      list.scrollTop = rowTop;
    } else if (rowBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = rowBottom - list.clientHeight;
    }
  }, [filtered, focusedId]);

  const visibleStart = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visibleEnd = Math.min(
    filtered.length,
    visibleStart + Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2,
  );

  return (
    <aside className="box-panel" aria-label="Annotation list">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{title}</span>
          <strong>{filtered.length} shown</strong>
        </div>
        <span className="panel-count">{totalCount}</span>
      </div>
      <label className="search-row">
        <span>Search boxes</span>
        <input
          type="search"
          value={search}
          placeholder="SKU, class, state, or ID"
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <div
        className="box-list"
        ref={listRef}
        role="listbox"
        aria-label="Boxes in spatial reading order"
        aria-multiselectable="true"
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      >
        <div className="box-list-spacer" style={{ height: filtered.length * ROW_HEIGHT }}>
          {filtered.slice(visibleStart, visibleEnd).map((box, visibleIndex) => (
            <button
              type="button"
              className={`box-row${selectedIds.has(box.id) ? " is-selected" : ""}`}
              style={{ top: (visibleStart + visibleIndex) * ROW_HEIGHT }}
              data-annotation-id={box.id}
              role="option"
              aria-selected={selectedIds.has(box.id)}
              onClick={(event) =>
                onSelect(box.id, event.shiftKey || event.ctrlKey || event.metaKey)
              }
              key={box.id}
            >
              <span className="state-marker" data-state={box.state}>
                {stateSymbol(box.state)}
              </span>
              <span className="box-copy">
                <strong>{skuLabel(box)}</strong>
                <span>
                  {box.id} · {box.classType.replace("_", " ")}
                </span>
              </span>
              <span className="box-confidence">
                {box.confidence === null ? "N/A" : `${Math.round(box.confidence * 100)}%`}
              </span>
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

function stateSymbol(state: AnnotationBox["state"]): string {
  const symbols: Record<AnnotationBox["state"], string> = {
    unverified: "?",
    verified: "✓",
    propagated: "⇉",
    flagged: "!",
  };
  return symbols[state];
}
