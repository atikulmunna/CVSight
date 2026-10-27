import { useEffect, useState } from "react";

import { loadSkuLineup, reviewItemCropUrl, type ReviewItem, type SkuLineup } from "./api";

type SkuLineupPanelProps = {
  datasetVersionId: string;
  skuId: string;
  item: ReviewItem;
};

type LineupState =
  | { status: "loading" }
  | { status: "loaded"; lineup: SkuLineup }
  | { status: "failed" };

// The workspace keys this panel by SKU and review item, so every item starts out loading.
export function SkuLineupPanel({ datasetVersionId, skuId, item }: SkuLineupPanelProps) {
  const [state, setState] = useState<LineupState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    loadSkuLineup(datasetVersionId, skuId)
      .then((lineup) => {
        if (active) {
          setState({ status: "loaded", lineup });
        }
      })
      .catch(() => {
        if (active) {
          setState({ status: "failed" });
        }
      });
    return () => {
      active = false;
    };
  }, [datasetVersionId, skuId]);

  const lineup = state.status === "loaded" ? state.lineup : null;
  const others = lineup?.items.filter((entry) => entry.annotationId !== item.annotationId) ?? [];

  return (
    <section className="review-lineup" aria-label="SKU lineup">
      <div className="review-lineup-heading">
        <h3>SKU lineup</h3>
        {lineup && <span>{lineup.skuName}</span>}
      </div>
      {lineup && lineup.references.length > 0 && (
        <div className="review-lineup-grid">
          {lineup.references.map((reference) => (
            <figure key={reference.id}>
              <img
                src={reference.thumbnailUrl}
                alt={`Reference photo ${reference.fileName}`}
                loading="lazy"
              />
              <figcaption>Reference</figcaption>
            </figure>
          ))}
        </div>
      )}
      <div className="review-lineup-grid">
        <figure className="is-current" aria-current="true">
          <img
            src={reviewItemCropUrl(item)}
            alt={`This box on ${item.imageName}`}
            loading="lazy"
          />
          <figcaption>This box</figcaption>
        </figure>
        {others.map((entry) => (
          <figure key={entry.annotationId}>
            <img
              src={entry.cropUrl}
              alt={`Box on ${entry.imageName}`}
              title={entry.imageName}
              loading="lazy"
            />
          </figure>
        ))}
      </div>
      {state.status === "loading" && (
        <p className="review-lineup-note">Loading other boxes with this SKU...</p>
      )}
      {state.status === "failed" && (
        <p className="review-lineup-note is-error">
          Other boxes with this SKU could not be loaded.
        </p>
      )}
      {lineup && others.length === 0 && (
        <p className="review-lineup-note">
          No other accepted box in this version carries this SKU yet.
        </p>
      )}
      {lineup?.truncated && (
        <p className="review-lineup-note">
          Showing the first {lineup.items.length} accepted boxes with this SKU.
        </p>
      )}
    </section>
  );
}
