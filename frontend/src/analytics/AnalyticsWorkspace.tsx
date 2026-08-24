import { useEffect, useState } from "react";

import {
  AnalyticsApiError,
  analyticsExportUrl,
  loadAnalytics,
  type AnalyticsReport,
  type ShareMetric,
} from "./api";

type AnalyticsWorkspaceProps = {
  datasetVersionId: string | null;
};

export function AnalyticsWorkspace({ datasetVersionId }: AnalyticsWorkspaceProps) {
  const [report, setReport] = useState<AnalyticsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!datasetVersionId) {
      return;
    }
    let active = true;
    loadAnalytics(datasetVersionId)
      .then((loaded) => {
        if (active) {
          setReport(loaded);
          setError(null);
        }
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(analyticsErrorMessage(requestError));
        }
      });
    return () => {
      active = false;
    };
  }, [datasetVersionId]);

  if (!datasetVersionId) {
    return (
      <section className="analytics-empty">
        <p className="eyebrow">Verified retail analytics</p>
        <h1>Open an immutable snapshot</h1>
        <p>Add a dataset version to the URL with <code>?version=&lt;uuid&gt;</code>.</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="analytics-empty">
        <p className="eyebrow">Verified retail analytics</p>
        <h1>Analytics unavailable</h1>
        <p>{error}</p>
      </section>
    );
  }
  if (!report) {
    return <section className="analytics-empty">Loading analytics...</section>;
  }

  return (
    <section className="analytics-workspace">
      <header className="analytics-heading">
        <div>
          <p className="eyebrow">Immutable snapshot</p>
          <h1>Retail analytics</h1>
          <p>
            Count share is primary. Every result carries its evidence status and
            reproducible lineage.
          </p>
        </div>
        <div className="analytics-export-actions">
          <a href={analyticsExportUrl(datasetVersionId, "csv")}>Export CSV</a>
          <a href={analyticsExportUrl(datasetVersionId, "json")}>Export JSON</a>
        </div>
      </header>

      <div className="analytics-summary" aria-label="Analytics summary">
        <SummaryCard label="Reviewed images" value={`${report.summary.reviewedImages}/${report.summary.images}`} />
        <SummaryCard label="Accepted facings" value={String(report.summary.acceptedProductFacings)} />
        <SummaryCard label="Reviewed gaps" value={String(report.summary.acceptedGaps)} />
        <SummaryCard label="Final share results" value={`${report.summary.finalCountShareImages}/${report.summary.images}`} />
      </div>

      <div className="analytics-lineage">
        <span>Formula <strong>{report.formulaVersion}</strong></span>
        <span>Snapshot <code>{shortHash(report.snapshotSha256)}</code></span>
        <span>Result <code>{shortHash(report.resultSha256)}</code></span>
      </div>

      {report.planogramStatus === "unsupported" && (
        <div className="analytics-notice">
          <strong>Planogram comparison unavailable</strong>
          <span>No effective fixture-matched planogram reference is stored.</span>
        </div>
      )}

      <div className="analytics-image-list">
        {report.images.map((image) => (
          <article className="analytics-image-card" key={image.imageId}>
            <div className="analytics-image-title">
              <div>
                <h2>{image.imageName}</h2>
                <span>{image.acceptedProductFacings} accepted facings · {image.acceptedGaps} gaps</span>
              </div>
              <MetricBadge metric={image.countShare} />
            </div>
            {image.countShare.isFinal ? (
              <ShareBars
                metric={image.countShare}
                labels={report.groupLabels}
              />
            ) : (
              <p className="analytics-incomplete">
                Count share is {image.countShare.status} and is not a final fact.
              </p>
            )}
            <div className="analytics-metric-foot">
              <span>Realogram: {image.realogramStatus}</span>
              <span>
                Image area: {image.imageAreaShare.status}
                {image.imageAreaShare.reason === "capture_view_not_declared"
                  ? " (capture view missing)"
                  : ""}
              </span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function MetricBadge({ metric }: { metric: ShareMetric }) {
  return (
    <span className={`analytics-status ${metric.isFinal ? "is-final" : "is-not-final"}`}>
      {metric.isFinal ? "Final" : "Not final"}
    </span>
  );
}

function ShareBars({
  metric,
  labels,
}: {
  metric: ShareMetric;
  labels: Record<string, string>;
}) {
  const shares = Object.entries(metric.shares).sort((first, second) => second[1] - first[1]);
  return (
    <div className="share-bars">
      {shares.map(([groupId, share]) => (
        <div className="share-row" key={groupId}>
          <span>{labels[groupId] ?? groupId}</span>
          <div><i style={{ width: `${share * 100}%` }} /></div>
          <strong>{Math.round(share * 100)}%</strong>
        </div>
      ))}
    </div>
  );
}

function analyticsErrorMessage(error: unknown): string {
  if (!(error instanceof AnalyticsApiError)) {
    return "Analytics request failed.";
  }
  if (error.code === "dataset_version_not_found") {
    return "That dataset version does not exist.";
  }
  if (error.code === "dataset_version_not_frozen") {
    return "Freeze the dataset version before generating analytics.";
  }
  return "Analytics request failed.";
}

function shortHash(value: string): string {
  return `${value.slice(0, 10)}…${value.slice(-6)}`;
}
