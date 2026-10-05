import { useState } from "react";

import {
  TRAINING_FORMATS,
  splitProblem,
  trainingExportUrl,
  type ClassMode,
  type ProjectExportType,
  type SplitPercentages,
  type TrainingFormat,
} from "./versionsApi";

const FORMAT_LABELS: Record<TrainingFormat, string> = {
  coco: "COCO JSON",
  yolo: "YOLO",
  csv: "CSV",
  createml: "CreateML JSON",
  tfrecord: "TFRecord",
};
const SPLIT_FIELDS: { key: keyof SplitPercentages; label: string }[] = [
  { key: "train", label: "Train %" },
  { key: "valid", label: "Valid %" },
  { key: "test", label: "Test %" },
];

export function DatasetExportForm({
  versionId,
  generated,
}: {
  versionId: string;
  generated: ProjectExportType[];
}) {
  const [format, setFormat] = useState<TrainingFormat>("yolo");
  const [classes, setClasses] = useState<ClassMode>("product");
  const [split, setSplit] = useState<SplitPercentages>({ train: 70, valid: 20, test: 10 });
  const problem = splitProblem(split);
  const previous = TRAINING_FORMATS.filter((item) => generated.includes(item));

  return (
    <fieldset className="dataset-export">
      <legend>Training dataset</legend>
      <div className="dataset-export-fields">
        <label>
          Format
          <select value={format} onChange={(event) => setFormat(event.target.value as TrainingFormat)}>
            {TRAINING_FORMATS.map((item) => (
              <option key={item} value={item}>
                {FORMAT_LABELS[item]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Classes
          <select value={classes} onChange={(event) => setClasses(event.target.value as ClassMode)}>
            <option value="product">Product</option>
            <option value="sku">SKU</option>
          </select>
        </label>
        {SPLIT_FIELDS.map(({ key, label }) => (
          <label key={key} className="dataset-export-split">
            {label}
            <input
              type="number"
              min={0}
              max={100}
              step={1}
              value={Number.isNaN(split[key]) ? "" : split[key]}
              onChange={(event) =>
                setSplit((current) => ({ ...current, [key]: event.target.valueAsNumber }))
              }
            />
          </label>
        ))}
      </div>
      {problem ? (
        <p className="dataset-export-problem" role="alert">
          {problem}
        </p>
      ) : (
        <a href={trainingExportUrl(versionId, format, classes, split)} download>
          Download {FORMAT_LABELS[format]} ZIP
        </a>
      )}
      <p className="dataset-export-note">
        Splits already recorded on photos are kept. Photos from the same store, visit, or
        near-duplicate group stay in one split, so test scores are not inflated.
        {previous.length > 0 &&
          ` Previously generated: ${previous.map((item) => FORMAT_LABELS[item]).join(", ")}.`}
      </p>
    </fieldset>
  );
}
