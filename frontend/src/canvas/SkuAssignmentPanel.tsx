import type { RefObject } from "react";

import { hierarchyPath, type Sku } from "../catalog/model";
import type { AnnotationBox } from "./model";

type SkuAssignmentPanelProps = {
  box: AnnotationBox;
  candidates: Sku[];
  query: string;
  recentSkus: Sku[];
  unknownSku: Sku | null;
  lastAssignedSku: Sku | null;
  source: "loading" | "live" | "fixture";
  searchInputRef: RefObject<HTMLInputElement | null>;
  variant?: "panel" | "popover";
  onQueryChange: (query: string) => void;
  onAssign: (sku: Sku) => void;
  onClose?: () => void;
};

export function SkuAssignmentPanel({
  box,
  candidates,
  query,
  recentSkus,
  unknownSku,
  lastAssignedSku,
  source,
  searchInputRef,
  variant = "panel",
  onQueryChange,
  onAssign,
  onClose,
}: SkuAssignmentPanelProps) {
  return (
    <section
      className={`sku-assignment-panel${variant === "popover" ? " is-popover" : ""}`}
      aria-label="SKU assignment"
      onWheel={(event) => event.stopPropagation()}
    >
      <div className="assignment-heading">
        <div>
          <span className="eyebrow">ASSIGN IDENTITY</span>
          <strong>{box.skuId ? box.sku : "No SKU assigned"}</strong>
        </div>
        <span className="catalog-source">
          {source === "loading"
            ? "Loading"
            : source === "live"
              ? "Live catalog"
              : "Local catalog"}
        </span>
        {onClose && (
          <button
            type="button"
            className="assignment-close"
            aria-label="Close SKU picker"
            onClick={onClose}
          >
            ×
          </button>
        )}
      </div>

      <label className="assignment-search">
        <span>Search SKUs <kbd>/</kbd></span>
        <input
          ref={searchInputRef}
          type="search"
          aria-label="SKU assignment search"
          placeholder="Name, UPC, brand, or variant"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>

      {recentSkus.length > 0 && (
        <div className="assignment-recents">
          <span>Recent</span>
          <div>
            {recentSkus.slice(0, 4).map((sku) => (
              <button type="button" onClick={() => onAssign(sku)} key={sku.id}>
                {sku.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="candidate-list" aria-label="SKU candidates">
        {candidates.map((sku, index) => (
          <button
            type="button"
            className={sku.id === box.skuId ? "is-assigned" : ""}
            onClick={() => onAssign(sku)}
            key={sku.id}
          >
            <span className="candidate-key">{index + 1}</span>
            {variant === "panel" &&
              (sku.referenceImages[0] ? (
                <img src={sku.referenceImages[0].thumbnailUrl} alt="" />
              ) : (
                <span className="reference-placeholder" aria-hidden="true">
                  {sku.name.charAt(0)}
                </span>
              ))}
            <span className="candidate-copy">
              <strong>{sku.name}</strong>
              <span>{hierarchyPath(sku) || sku.upc || "No catalog hierarchy"}</span>
            </span>
          </button>
        ))}
        {candidates.length === 0 && (
          <p className="assignment-empty">No active SKU matches this search.</p>
        )}
      </div>

      {variant === "panel" && <ReferenceImages sku={candidates[0] ?? null} />}

      <div className="assignment-fallbacks">
        <button
          type="button"
          onClick={() => lastAssignedSku && onAssign(lastAssignedSku)}
          disabled={!lastAssignedSku}
        >
          Same as previous <kbd>S</kbd>
        </button>
        <button
          type="button"
          className="unknown-action"
          onClick={() => unknownSku && onAssign(unknownSku)}
          disabled={!unknownSku}
        >
          Unknown / Other <kbd>U</kbd>
        </button>
      </div>
    </section>
  );
}

function ReferenceImages({ sku }: { sku: Sku | null }) {
  return (
    <div className="assignment-references">
      <span>Reference images</span>
      {sku && sku.referenceImages.length > 0 ? (
        <div>
          {sku.referenceImages.slice(0, 4).map((reference) => (
            <img
              src={reference.thumbnailUrl}
              alt={`${sku.name} reference`}
              key={reference.id}
            />
          ))}
        </div>
      ) : (
        <p>{sku ? "No references for this candidate" : "Choose a candidate"}</p>
      )}
    </div>
  );
}
