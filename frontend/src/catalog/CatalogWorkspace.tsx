import { useEffect, useMemo, useRef, useState } from "react";

import {
  CatalogApiError,
  createSku,
  deprecateSku,
  loadCatalog,
  mergeSku,
  updateSku,
  uploadReferenceImage,
  type SkuFields,
} from "./api";
import { DEMO_SKUS } from "./demoCatalog";
import { hierarchyPath, searchSkus, type Sku } from "./model";

type CatalogWorkspaceProps = {
  fallbackSkus?: Sku[];
};

type CatalogView = "all" | "favorites" | "recent";
type FormMode = "create" | "edit" | null;

const ROW_HEIGHT = 58;
const OVERSCAN = 6;
const FAVORITES_KEY = "shelfsight:sku-favorites";
const RECENTS_KEY = "shelfsight:sku-recents";

export function CatalogWorkspace({
  fallbackSkus = DEMO_SKUS,
}: CatalogWorkspaceProps) {
  const [skus, setSkus] = useState(fallbackSkus);
  const [source, setSource] = useState<"loading" | "live" | "fixture">("loading");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<CatalogView>("all");
  const [selectedId, setSelectedId] = useState(fallbackSkus[0]?.id ?? null);
  const [favorites, setFavorites] = useState(() => storedIds(FAVORITES_KEY));
  const [recents, setRecents] = useState(() => storedIds(RECENTS_KEY));
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(700);
  const [formMode, setFormMode] = useState<FormMode>(null);
  const [mergeQuery, setMergeQuery] = useState("");
  const [notice, setNotice] = useState("Loading catalog");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void loadCatalog((input, init) =>
      fetch(input, { ...init, signal: controller.signal }),
    )
      .then((loaded) => {
        setSkus(loaded);
        setSelectedId(loaded[0]?.id ?? null);
        setSource("live");
        setNotice(`${loaded.length.toLocaleString()} SKUs loaded`);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setSource("fixture");
        setNotice("API unavailable. Showing the read-only 2,001 SKU fixture");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const resize = () =>
      setViewportHeight(listRef.current?.clientHeight || 700);
    resize();
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  const visibleCatalog = useMemo(() => {
    const allowed =
      view === "favorites"
        ? new Set(favorites)
        : view === "recent"
          ? new Set(recents)
          : null;
    const scoped = allowed
      ? skus.filter((sku) => allowed.has(sku.id))
      : skus;
    return searchSkus(scoped, query);
  }, [favorites, query, recents, skus, view]);
  const selected = skus.find((sku) => sku.id === selectedId) ?? null;
  const visibleStart = Math.max(
    0,
    Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN,
  );
  const visibleEnd = Math.min(
    visibleCatalog.length,
    visibleStart + Math.ceil(viewportHeight / ROW_HEIGHT) + OVERSCAN * 2,
  );
  const rows = visibleCatalog.slice(visibleStart, visibleEnd);
  const mergeCandidates = useMemo(
    () =>
      searchSkus(
        skus.filter(
          (sku) =>
            sku.status === "active" &&
            !sku.isUnknown &&
            sku.id !== selected?.id,
        ),
        mergeQuery,
      ).slice(0, 6),
    [mergeQuery, selected?.id, skus],
  );

  function selectSku(id: string) {
    setSelectedId(id);
    const next = [id, ...recents.filter((recent) => recent !== id)].slice(0, 12);
    setRecents(next);
    storeIds(RECENTS_KEY, next);
  }

  function toggleFavorite(id: string) {
    const next = favorites.includes(id)
      ? favorites.filter((favorite) => favorite !== id)
      : [id, ...favorites];
    setFavorites(next);
    storeIds(FAVORITES_KEY, next);
  }

  async function saveFields(fields: SkuFields) {
    if (source !== "live") {
      return;
    }
    setBusy(true);
    try {
      const saved =
        formMode === "create"
          ? await createSku(fields)
          : await updateSku(selected!.id, fields);
      setSkus((current) =>
        formMode === "create"
          ? [...current, saved]
          : current.map((sku) => (sku.id === saved.id ? saved : sku)),
      );
      setSelectedId(saved.id);
      setFormMode(null);
      setNotice(formMode === "create" ? "SKU created" : "SKU updated");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function deprecateSelected() {
    if (!selected || source !== "live") {
      return;
    }
    setBusy(true);
    try {
      const deprecated = await deprecateSku(selected.id);
      setSkus((current) =>
        current.map((sku) => (sku.id === deprecated.id ? deprecated : sku)),
      );
      setNotice("SKU deprecated");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function mergeSelected(target: Sku) {
    if (!selected || source !== "live") {
      return;
    }
    setBusy(true);
    try {
      const merged = await mergeSku(selected.id, target.id);
      setSkus((current) =>
        current.map((sku) => {
          if (sku.id === merged.source.id) {
            return merged.source;
          }
          return sku.id === merged.target.id ? merged.target : sku;
        }),
      );
      setSelectedId(merged.target.id);
      setMergeQuery("");
      setNotice(
        `Merged SKU and repointed ${merged.repointedAnnotations} annotations`,
      );
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function uploadReference(file: File) {
    if (!selected || source !== "live") {
      return;
    }
    setBusy(true);
    try {
      const reference = await uploadReferenceImage(selected.id, file);
      setSkus((current) =>
        current.map((sku) =>
          sku.id === selected.id
            ? {
                ...sku,
                referenceImages: [...sku.referenceImages, reference],
              }
            : sku,
        ),
      );
      setNotice("Reference image added");
    } catch (error) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="catalog-workspace">
      <aside className="catalog-sidebar" aria-label="SKU catalog search">
        <div className="catalog-summary">
          <span className="eyebrow">SKU CATALOG</span>
          <strong>{visibleCatalog.length.toLocaleString()} results</strong>
          <span className="catalog-source" data-source={source}>
            {source}
          </span>
        </div>
        <label className="catalog-search">
          <span>Search name, UPC, hierarchy, or variant</span>
          <input
            type="search"
            value={query}
            placeholder="Try aur zro 330"
            onChange={(event) => {
              setQuery(event.target.value);
              setScrollTop(0);
            }}
          />
        </label>
        <div className="catalog-views" aria-label="Catalog views">
          {(["all", "favorites", "recent"] as const).map((catalogView) => (
            <button
              type="button"
              className={view === catalogView ? "is-active" : ""}
              onClick={() => setView(catalogView)}
              key={catalogView}
            >
              {catalogView}
            </button>
          ))}
        </div>
        <div
          className="catalog-list"
          ref={listRef}
          role="listbox"
          aria-label="Searchable SKU results"
          onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        >
          <div
            className="catalog-list-spacer"
            style={{ height: visibleCatalog.length * ROW_HEIGHT }}
          >
            {rows.map((sku, visibleIndex) => {
              const index = visibleStart + visibleIndex;
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={sku.id === selectedId}
                  className={`catalog-row${sku.id === selectedId ? " is-selected" : ""}`}
                  style={{ top: index * ROW_HEIGHT }}
                  onClick={() => selectSku(sku.id)}
                  key={sku.id}
                >
                  <ReferenceThumb sku={sku} />
                  <span>
                    <strong>{sku.name}</strong>
                    <small>{hierarchyPath(sku) || "Explicit fallback target"}</small>
                  </span>
                  <span className="sku-status">{sku.status}</span>
                </button>
              );
            })}
          </div>
        </div>
      </aside>

      <section className="catalog-detail" aria-label="SKU details">
        <header className="catalog-detail-header">
          <div>
            <span className="eyebrow">CATALOG RECORD</span>
            <strong>{selected?.name ?? "No SKU selected"}</strong>
          </div>
          <div>
            <button
              type="button"
              onClick={() => setFormMode("create")}
              disabled={source !== "live" || busy}
            >
              New SKU
            </button>
            {selected && (
              <button
                type="button"
                aria-label={
                  favorites.includes(selected.id)
                    ? "Remove favorite"
                    : "Add favorite"
                }
                onClick={() => toggleFavorite(selected.id)}
              >
                {favorites.includes(selected.id) ? "★" : "☆"}
              </button>
            )}
          </div>
        </header>
        <p className="catalog-notice" role="status">
          {notice}
        </p>
        {formMode ? (
          <SkuForm
            sku={formMode === "edit" ? selected : null}
            busy={busy}
            onSubmit={(fields) => void saveFields(fields)}
            onCancel={() => setFormMode(null)}
          />
        ) : selected ? (
          <SkuDetails
            sku={selected}
            live={source === "live"}
            busy={busy}
            mergeQuery={mergeQuery}
            mergeCandidates={mergeCandidates}
            onEdit={() => setFormMode("edit")}
            onDeprecate={() => void deprecateSelected()}
            onMergeQuery={setMergeQuery}
            onMerge={(target) => void mergeSelected(target)}
            onReference={(file) => void uploadReference(file)}
          />
        ) : (
          <p className="empty-state">Choose a SKU from the search results.</p>
        )}
      </section>
    </main>
  );
}

function ReferenceThumb({ sku }: { sku: Sku }) {
  const reference = sku.referenceImages[0];
  return reference ? (
    <img src={reference.thumbnailUrl} alt="" />
  ) : (
    <span className="reference-placeholder" aria-hidden="true">
      {sku.isUnknown ? "?" : sku.name.slice(0, 1).toUpperCase()}
    </span>
  );
}

function SkuDetails({
  sku,
  live,
  busy,
  mergeQuery,
  mergeCandidates,
  onEdit,
  onDeprecate,
  onMergeQuery,
  onMerge,
  onReference,
}: {
  sku: Sku;
  live: boolean;
  busy: boolean;
  mergeQuery: string;
  mergeCandidates: Sku[];
  onEdit: () => void;
  onDeprecate: () => void;
  onMergeQuery: (query: string) => void;
  onMerge: (target: Sku) => void;
  onReference: (file: File) => void;
}) {
  const editable = live && sku.status === "active" && !sku.isUnknown && !busy;
  return (
    <div className="sku-details">
      <div className="sku-actions">
        <button type="button" onClick={onEdit} disabled={!editable}>
          Edit
        </button>
        <button type="button" onClick={onDeprecate} disabled={!editable}>
          Deprecate
        </button>
      </div>
      <dl>
        <Detail label="Status" value={sku.status} />
        <Detail label="UPC" value={sku.upc ?? "Not set"} />
        <Detail label="Category" value={sku.category ?? "Not set"} />
        <Detail label="Subcategory" value={sku.subcategory ?? "Not set"} />
        <Detail label="Brand" value={sku.brand ?? "Not set"} />
        <Detail label="Variant" value={sku.variant ?? "Not set"} />
      </dl>
      <section className="reference-section">
        <div>
          <span className="eyebrow">REFERENCE IMAGES</span>
          <strong>{sku.referenceImages.length} images</strong>
        </div>
        <div className="reference-grid">
          {sku.referenceImages.map((reference) => (
            <figure key={reference.id}>
              <img src={reference.thumbnailUrl} alt="" />
              <figcaption>{reference.originalFilename}</figcaption>
            </figure>
          ))}
        </div>
        <label className="reference-upload">
          Add validated image
          <input
            type="file"
            accept="image/jpeg,image/png"
            disabled={!editable}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                onReference(file);
              }
            }}
          />
        </label>
      </section>
      {!sku.isUnknown && sku.status === "active" && (
        <section className="merge-section">
          <span className="eyebrow">MERGE DUPLICATE</span>
          <label>
            Find the canonical target
            <input
              value={mergeQuery}
              placeholder="Search target SKU"
              disabled={!live || busy}
              onChange={(event) => onMergeQuery(event.target.value)}
            />
          </label>
          {mergeQuery && (
            <div className="merge-results">
              {mergeCandidates.map((candidate) => (
                <button
                  type="button"
                  onClick={() => onMerge(candidate)}
                  disabled={!live || busy}
                  key={candidate.id}
                >
                  <strong>{candidate.name}</strong>
                  <span>{hierarchyPath(candidate)}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function SkuForm({
  sku,
  busy,
  onSubmit,
  onCancel,
}: {
  sku: Sku | null;
  busy: boolean;
  onSubmit: (fields: SkuFields) => void;
  onCancel: () => void;
}) {
  const [fields, setFields] = useState<SkuFields>(() => fieldsFromSku(sku));
  function change(field: keyof SkuFields, value: string) {
    setFields((current) => ({ ...current, [field]: value || null }));
  }
  return (
    <form
      className="sku-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ ...fields, name: fields.name.trim() });
      }}
    >
      <label>
        Name
        <input
          required
          maxLength={255}
          value={fields.name}
          onChange={(event) =>
            setFields((current) => ({ ...current, name: event.target.value }))
          }
        />
      </label>
      {(["upc", "category", "subcategory", "brand", "variant"] as const).map(
        (field) => (
          <label key={field}>
            {field}
            <input
              value={fields[field] ?? ""}
              maxLength={field === "upc" ? 14 : 255}
              inputMode={field === "upc" ? "numeric" : undefined}
              pattern={
                field === "upc" ? "([0-9]{8}|[0-9]{12,14})?" : undefined
              }
              onChange={(event) => change(field, event.target.value)}
            />
          </label>
        ),
      )}
      <div>
        <button type="submit" className="primary-action" disabled={busy}>
          {sku ? "Save changes" : "Create SKU"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function fieldsFromSku(sku: Sku | null): SkuFields {
  return {
    name: sku?.name ?? "",
    upc: sku?.upc ?? null,
    category: sku?.category ?? null,
    subcategory: sku?.subcategory ?? null,
    brand: sku?.brand ?? null,
    variant: sku?.variant ?? null,
  };
}

function storedIds(key: string): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string").slice(0, 100)
      : [];
  } catch {
    return [];
  }
}

function storeIds(key: string, ids: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch {
    return;
  }
}

function errorMessage(error: unknown): string {
  if (!(error instanceof CatalogApiError)) {
    return "Catalog request failed";
  }
  const messages: Record<string, string> = {
    duplicate_upc: "UPC already belongs to another SKU",
    duplicate_reference_image: "This reference image is already attached",
    corrupt_image: "Reference image is corrupt",
    unsupported_image: "Only JPEG and PNG reference images are supported",
    invalid_sku_state: "This SKU state does not allow that action",
  };
  return messages[error.code] ?? "Catalog request failed";
}
