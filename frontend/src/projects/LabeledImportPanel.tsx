import { useState } from "react";

import { createSku } from "../catalog/api";
import { ProjectImageApiError } from "./imagesApi";
import {
  previewLabeledImport,
  runLabeledImportPage,
  type AnnotationState,
  type LabeledFormat,
  type LabeledOutcome,
  type LabeledPreview,
} from "./labeledImportApi";

type ClassChoice = "suggested" | "create" | "none";

type Phase =
  | { name: "form" }
  | { name: "previewing" }
  | { name: "importing"; done: number; total: number }
  | { name: "finished"; outcomes: LabeledOutcome[] };

const ERROR_MESSAGES: Record<string, string> = {
  dataset_not_found: "Nothing found at that path. Use a folder for YOLO or a .json file for COCO.",
  unsafe_path: "The path must be relative to the import folder, with forward slashes.",
  missing_data_yaml: "A YOLO dataset needs data.yaml at its root.",
  invalid_data_yaml: "data.yaml could not be read.",
  invalid_class_names: "The class names must be unique and non-empty.",
  invalid_coco: "The COCO file could not be read.",
  image_not_found: "The COCO file names an image that is not on disk.",
  too_many_images: "Import at most 50,000 images at a time.",
  unmapped_classes: "Every class needs a SKU choice.",
  inactive_sku: "A chosen SKU is no longer active in the catalog.",
  dataset_version_frozen: "This version is frozen. Start a new working version first.",
};

export function LabeledImportPanel({
  projectId,
  versionId,
  onClose,
  onImported,
}: {
  projectId: string;
  versionId: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [format, setFormat] = useState<LabeledFormat>("yolo");
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState<LabeledPreview | null>(null);
  const [choices, setChoices] = useState<Record<string, ClassChoice>>({});
  const [state, setState] = useState<AnnotationState>("verified");
  const [phase, setPhase] = useState<Phase>({ name: "form" });
  const [error, setError] = useState<string | null>(null);
  const busy = phase.name === "previewing" || phase.name === "importing";

  async function loadPreview() {
    setError(null);
    setPreview(null);
    setPhase({ name: "previewing" });
    try {
      const result = await previewLabeledImport(projectId, versionId, format, path.trim());
      setPreview(result);
      setChoices(
        Object.fromEntries(
          result.classes.map((item) => [item.name, item.suggestedSkuId ? "suggested" : "create"]),
        ),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPhase({ name: "form" });
    }
  }

  async function runImport() {
    if (!preview) {
      return;
    }
    setError(null);
    setPhase({ name: "importing", done: 0, total: preview.images });
    try {
      const classSkus: Record<string, string | null> = {};
      for (const item of preview.classes) {
        const choice = choices[item.name] ?? "none";
        classSkus[item.name] =
          choice === "suggested"
            ? item.suggestedSkuId
            : choice === "create"
              ? (await createSku(newSku(item.name))).id
              : null;
      }
      const outcomes: LabeledOutcome[] = [];
      for (let offset: number | null = 0; offset !== null; ) {
        const page = await runLabeledImportPage(projectId, versionId, {
          format,
          path: path.trim(),
          classSkus,
          annotationState: state,
          offset,
        });
        outcomes.push(...page.results);
        setPhase({ name: "importing", done: outcomes.length, total: page.totalImages });
        offset = page.nextOffset;
      }
      setPhase({ name: "finished", outcomes });
      onImported();
    } catch (caught) {
      setError(errorMessage(caught));
      setPhase({ name: "form" });
    }
  }

  return (
    <section className="image-upload-panel labeled-import-panel" aria-label="Import labeled dataset">
      <div className="image-upload-panel-heading">
        <div>
          <h2>Import a labeled dataset</h2>
          <p>
            Reads a YOLO folder or COCO file from the server's import folder. Source train,
            valid, and test folders are kept as <code>source_split</code>; CVSight's own split
            stays unset so random source splits cannot leak into exports.
          </p>
        </div>
        <button type="button" onClick={onClose} disabled={busy} aria-label="Close import panel">
          ×
        </button>
      </div>

      <div className="labeled-import-source">
        <label>
          <span>Format</span>
          <select
            value={format}
            disabled={busy}
            onChange={(event) => {
              setFormat(event.currentTarget.value as LabeledFormat);
              setPreview(null);
            }}
          >
            <option value="yolo">YOLO folder</option>
            <option value="coco">COCO JSON file</option>
          </select>
        </label>
        <label>
          <span>Path inside the import folder</span>
          <input
            value={path}
            disabled={busy}
            placeholder={format === "yolo" ? "datasets/shelf-audit" : "datasets/shelf/_annotations.coco.json"}
            onChange={(event) => {
              setPath(event.currentTarget.value);
              setPreview(null);
            }}
          />
        </label>
        <button type="button" onClick={() => void loadPreview()} disabled={busy || !path.trim()}>
          {phase.name === "previewing" ? "Reading" : "Preview"}
        </button>
      </div>

      {error && <div className="create-project-error" role="alert">{error}</div>}

      {preview && phase.name !== "finished" && (
        <>
          <p className="labeled-import-summary">
            {preview.images} images, {preview.boxes} boxes
            {preview.skippedLabels > 0 && `, ${preview.skippedLabels} unreadable labels skipped`}.
            Source splits:{" "}
            {Object.entries(preview.sourceSplits)
              .map(([split, images]) => `${split} ${images}`)
              .join(", ")}
            .
          </p>
          <table className="labeled-import-classes">
            <thead>
              <tr>
                <th>Class</th>
                <th>Boxes</th>
                <th>SKU</th>
              </tr>
            </thead>
            <tbody>
              {preview.classes.map((item) => (
                <tr key={item.name}>
                  <td>{item.name}</td>
                  <td>{item.boxes}</td>
                  <td>
                    <select
                      aria-label={`SKU for ${item.name}`}
                      value={choices[item.name] ?? "none"}
                      disabled={busy}
                      onChange={(event) =>
                        setChoices({ ...choices, [item.name]: event.currentTarget.value as ClassChoice })
                      }
                    >
                      {item.suggestedSkuId && <option value="suggested">Catalog SKU {item.name}</option>}
                      <option value="create">Create SKU {item.name}</option>
                      <option value="none">No SKU</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <fieldset className="labeled-import-state" disabled={busy}>
            <legend>Imported boxes are</legend>
            <label>
              <input
                type="radio"
                name="annotation-state"
                checked={state === "verified"}
                onChange={() => setState("verified")}
              />
              Ground truth (verified)
            </label>
            <label>
              <input
                type="radio"
                name="annotation-state"
                checked={state === "proposed"}
                onChange={() => setState("proposed")}
              />
              Proposals that need review
            </label>
          </fieldset>
          <div className="image-upload-actions">
            <span>
              {phase.name === "importing" ? `Imported ${phase.done} of ${phase.total} images` : ""}
            </span>
            <button
              className="projects-primary-action"
              type="button"
              onClick={() => void runImport()}
              disabled={busy || preview.images === 0}
            >
              {phase.name === "importing" ? "Importing" : `Import ${preview.images} images`}
            </button>
          </div>
        </>
      )}

      {phase.name === "finished" && <ImportSummary outcomes={phase.outcomes} />}
    </section>
  );
}

function ImportSummary({ outcomes }: { outcomes: LabeledOutcome[] }) {
  const counts = { imported: 0, completed: 0, skipped: 0, rejected: 0 };
  let boxes = 0;
  const reasons = new Map<string, number>();
  for (const item of outcomes) {
    counts[item.outcome] += 1;
    boxes += item.boxes;
    if (item.outcome === "rejected") {
      reasons.set(item.code, (reasons.get(item.code) ?? 0) + 1);
    }
  }
  return (
    <p className="labeled-import-summary" role="status">
      Imported {counts.imported + counts.completed} images with {boxes} boxes.
      {counts.skipped > 0 && ` ${counts.skipped} were already labeled and left unchanged.`}
      {counts.rejected > 0 &&
        ` ${counts.rejected} were rejected (${[...reasons]
          .map(([code, total]) => `${code.replaceAll("_", " ")}: ${total}`)
          .join(", ")}).`}
    </p>
  );
}

function newSku(name: string) {
  return { name, upc: null, category: null, subcategory: null, brand: null, variant: null };
}

function errorMessage(error: unknown): string {
  if (error instanceof ProjectImageApiError) {
    if (error.status === 403) {
      return "Only project owners can import labeled datasets.";
    }
    return ERROR_MESSAGES[error.code] ?? "The import could not be completed. Check the API and try again.";
  }
  return "The import could not be completed. Check the API and try again.";
}
