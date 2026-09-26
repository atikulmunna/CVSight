import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AnalyticsWorkspace } from "./analytics/AnalyticsWorkspace";
import type { AuthSession } from "./auth/api";
import { AnnotationWorkspace } from "./canvas/AnnotationWorkspace";
import { DEMO_FIXTURE } from "./canvas/demoFixture";
import { loadLiveFixture } from "./canvas/liveFixture";
import { PropagationWorkspace } from "./canvas/PropagationWorkspace";
import {
  parseCanvasFixture,
  type AnnotationBox,
  type CanvasFixture,
} from "./canvas/model";
import { CatalogWorkspace } from "./catalog/CatalogWorkspace";
import { GapReviewNavigator } from "./GapReviewNavigator";
import { loadGapReviewManifest, type GapReviewManifest } from "./gapReview";
import { ImageReviewBar } from "./ImageReviewBar";
import { loadImageReviewed, markImageReviewed } from "./imageReview";
import { loadImageNeighbors, type ImageNeighbors } from "./projects/imagesApi";
import { ReviewWorkspace } from "./review/ReviewWorkspace";
import type { SaveStatus } from "./canvas/useAnnotationAutosave";
import { workspacesForRole, type Workspace } from "./workspace";

type ApiStatus = "checking" | "online" | "unavailable";

type HealthResponse = {
  status: "ok";
  service: "shelfsight-api";
};

function isHealthResponse(value: unknown): value is HealthResponse {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return candidate.status === "ok" && candidate.service === "shelfsight-api";
}

type AppProps = {
  currentUser?: AuthSession;
  onLogout?: () => Promise<void>;
  datasetVersionId?: string | null;
  imageId?: string | null;
  initialWorkspace?: Workspace;
  projectLabel?: string;
  onExitWorkspace?: () => void;
  onWorkspaceChange?: (workspace: Workspace) => void;
  datasetId?: string;
  onOpenImage?: (imageId: string) => void;
};

export default function App({
  currentUser,
  onLogout,
  datasetVersionId: routedDatasetVersionId,
  imageId: routedImageId,
  initialWorkspace,
  projectLabel,
  onExitWorkspace,
  onWorkspaceChange,
  datasetId,
  onOpenImage,
}: AppProps) {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");
  const [fixture, setFixture] = useState<CanvasFixture>(DEMO_FIXTURE);
  const [liveAnnotations, setLiveAnnotations] = useState<AnnotationBox[]>(
    DEMO_FIXTURE.annotations,
  );
  const [fixtureError, setFixtureError] = useState(false);
  const [gapReview, setGapReview] = useState<GapReviewManifest | null>(null);
  const [gapReviewIndex, setGapReviewIndex] = useState(0);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [imageReviewed, setImageReviewed] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [neighbors, setNeighbors] = useState<ImageNeighbors | null>(null);
  const [saveStatus, setSaveStatus] =
    useState<SaveStatus>("saved");
  const flushRef = useRef<(() => Promise<void>) | null>(null);
  const datasetVersionId = useMemo(() => {
    if (routedDatasetVersionId !== undefined) {
      return routedDatasetVersionId;
    }
    return new URLSearchParams(window.location.search).get("version");
  }, [routedDatasetVersionId]);
  const requestedWorkspace = useMemo(
    () =>
      initialWorkspace ??
      new URLSearchParams(window.location.search).get("workspace"),
    [initialWorkspace],
  );
  const allowedWorkspaces = workspacesForRole(currentUser?.role ?? "owner");
  const [workspace, setWorkspace] = useState<Workspace>(() =>
    requestedWorkspace &&
    allowedWorkspaces.includes(requestedWorkspace as Workspace)
      ? (requestedWorkspace as Workspace)
      : datasetVersionId && allowedWorkspaces.includes("review")
      ? "review"
      : allowedWorkspaces[0]!,
  );

  useEffect(() => {
    const controller = new AbortController();

    async function checkApi() {
      try {
        const response = await fetch("/api/health", {
          signal: controller.signal,
          headers: { Accept: "application/json" },
        });
        const body: unknown = await response.json();
        setApiStatus(response.ok && isHealthResponse(body) ? "online" : "unavailable");
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setApiStatus("unavailable");
      }
    }

    void checkApi();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const parameters = new URLSearchParams(window.location.search);
    const imageId = routedImageId ?? parameters.get("image");
    const localFixture = parameters.get("fixture") === "local";
    const benchmark = parameters.get("benchmark");
    const gapReviewRequested = benchmark === "gap" || benchmark === "gap-truth";
    if (!imageId && !localFixture && !gapReviewRequested) {
      return;
    }
    const controller = new AbortController();

    async function loadRequestedFixture() {
      try {
        let parsed: CanvasFixture;
        if (gapReviewRequested) {
          const manifest = await loadGapReviewManifest(
            benchmark === "gap-truth"
              ? "/local-fixtures/gap-truth.json"
              : "/local-fixtures/gap-review.json",
            (input, init) => fetch(input, { ...init, signal: controller.signal }),
          );
          const requestedIndex = Number(parameters.get("item") ?? "1") - 1;
          const index = Number.isInteger(requestedIndex)
            ? Math.min(Math.max(requestedIndex, 0), manifest.items.length - 1)
            : 0;
          parsed = await loadLiveFixture(
            manifest.items[index]!.imageId,
            (input, init) => fetch(input, { ...init, signal: controller.signal }),
          );
          setGapReview(manifest);
          setGapReviewIndex(index);
          setImageReviewed(
            await loadImageReviewed(
              manifest.items[index]!.imageId,
              (input, init) => fetch(input, { ...init, signal: controller.signal }),
            ),
          );
        } else if (imageId) {
          parsed = await loadLiveFixture(imageId, (input, init) =>
            fetch(input, { ...init, signal: controller.signal }),
          );
          if (routedImageId) {
            setImageReviewed(
              await loadImageReviewed(routedImageId, (input, init) =>
                fetch(input, { ...init, signal: controller.signal }),
              ),
            );
          }
        } else {
          const response = await fetch("/local-fixtures/t007.json", {
            signal: controller.signal,
            headers: { Accept: "application/json" },
          });
          if (!response.ok) {
            throw new Error("local fixture request failed");
          }
          const body: unknown = await response.json();
          parsed = parseCanvasFixture(body);
        }
        setFixtureError(false);
        setFixture(parsed);
        setLiveAnnotations(parsed.annotations);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setFixtureError(true);
      }
    }

    void loadRequestedFixture();
    return () => controller.abort();
  }, [routedImageId]);

  function changeWorkspace(nextWorkspace: Workspace) {
    setWorkspace(nextWorkspace);
    onWorkspaceChange?.(nextWorkspace);
  }

  const handleAnnotationsChange = useCallback((annotations: AnnotationBox[]) => {
    setLiveAnnotations(annotations);
  }, []);
  const handleSaveStateChange = useCallback(
    (status: SaveStatus, flush: () => Promise<void>) => {
      setSaveStatus(status);
      if (status === "saving") {
        setImageReviewed(false);
      }
      flushRef.current = flush;
    },
    [],
  );

  async function selectGapReviewImage(index: number) {
    if (!gapReview || index < 0 || index >= gapReview.items.length) {
      return;
    }
    try {
      setReviewBusy(true);
      const parsed = await loadLiveFixture(gapReview.items[index]!.imageId);
      const reviewed = await loadImageReviewed(
        gapReview.items[index]!.imageId,
      );
      setFixture(parsed);
      setLiveAnnotations(parsed.annotations);
      setGapReviewIndex(index);
      setImageReviewed(reviewed);
      setReviewError(null);
      setFixtureError(false);
      const url = new URL(window.location.href);
      url.searchParams.set(
        "benchmark",
        gapReview.reviewMode === "blind-truth" ? "gap-truth" : "gap",
      );
      url.searchParams.set("item", String(index + 1));
      window.history.replaceState(null, "", url);
    } catch {
      setFixtureError(true);
    } finally {
      setReviewBusy(false);
    }
  }

  // The gap benchmark walks its own manifest; a project route reviews the image it opened.
  const reviewImageId = gapReview
    ? gapReview.items[gapReviewIndex]!.imageId
    : routedImageId ?? null;
  const reviewImageLoaded =
    reviewImageId !== null && fixture.images.some((image) => image.id === reviewImageId);

  // Photo navigation follows the project's image grid; it needs the project route.
  const canNavigate = Boolean(onOpenImage);
  useEffect(() => {
    if (!routedImageId || !datasetId || !datasetVersionId || !canNavigate) {
      return;
    }
    const controller = new AbortController();
    loadImageNeighbors(datasetId, datasetVersionId, routedImageId, (input, init) =>
      fetch(input, { ...init, signal: controller.signal }),
    )
      .then(setNeighbors)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setReviewError("Photo navigation is unavailable for this image.");
      });
    return () => controller.abort();
  }, [canNavigate, datasetId, datasetVersionId, routedImageId]);

  async function openNeighbor(imageId: string) {
    try {
      setReviewBusy(true);
      await flushRef.current?.();
      onOpenImage?.(imageId);
    } catch {
      setReviewError("Changes could not be saved, so this photo stays open.");
    } finally {
      setReviewBusy(false);
    }
  }

  async function markCurrentImageReviewed(thenNext = false) {
    if (!reviewImageId) {
      return;
    }
    try {
      setReviewBusy(true);
      await flushRef.current?.();
      await markImageReviewed(reviewImageId);
      setImageReviewed(true);
      setReviewError(null);
      if (thenNext && neighbors?.nextId) {
        onOpenImage?.(neighbors.nextId);
      }
    } catch (error) {
      setReviewError(
        error instanceof Error ? error.message : "Image review could not be saved.",
      );
    } finally {
      setReviewBusy(false);
    }
  }

  const progress = useMemo(() => {
    const active = liveAnnotations.filter(
      (box) => box.lifecycleState !== "rejected",
    );
    const verified = active.filter((box) => box.state === "verified").length;
    return {
      verified,
      total: active.length,
      percent: active.length === 0 ? 0 : Math.round((verified / active.length) * 100),
    };
  }, [liveAnnotations]);
  const assignmentProgress = useMemo(() => {
    const targets = liveAnnotations.filter(
      (box) =>
        box.classType === "product" &&
        box.lifecycleState === "verified" &&
        box.reviewState === "accepted",
    );
    return {
      assigned: targets.filter((box) => box.skuId !== null).length,
      total: targets.length,
    };
  }, [liveAnnotations]);
  const unresolvedCount = useMemo(
    () =>
      liveAnnotations.filter(
        (box) =>
          box.lifecycleState !== "rejected" &&
          !(
            box.lifecycleState === "verified" &&
            box.reviewState === "accepted"
          ),
      ).length,
    [liveAnnotations],
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <a className="brand-lockup" href="/projects" aria-label="CVSight projects">
            <img className="brand-mark" src="/cvsight-mark.svg" alt="" />
            <span className="brand">
              CV<span>Sight</span>
            </span>
          </a>
          {onExitWorkspace ? (
            <button className="project-path project-path-button" type="button" onClick={onExitWorkspace}>
              {projectLabel ?? "Project"}
              {routedImageId
                ? ` / ${fixture.name.toLowerCase().replaceAll(" ", "-")}`
                : ""}
            </button>
          ) : (
            <span className="project-path">
              retail-q3-audit / {fixture.name.toLowerCase().replaceAll(" ", "-")}
            </span>
          )}
        </div>
        <nav className="mode-tabs" aria-label="Workspace">
          {gapReview ? (
            <button type="button" className="is-active">
              {gapReview.reviewMode === "blind-truth" ? "Label gaps" : "Review gaps"}
            </button>
          ) : (
            <>
          {allowedWorkspaces.includes("verify") && <button
            type="button"
            className={workspace === "verify" ? "is-active" : ""}
            onClick={() => changeWorkspace("verify")}
          >
            Verify boxes
          </button>}
          {allowedWorkspaces.includes("assign") && <button
            type="button"
            className={workspace === "assign" ? "is-active" : ""}
            onClick={() => changeWorkspace("assign")}
          >
            Assign SKUs
          </button>}
          {allowedWorkspaces.includes("propagate") && <button
            type="button"
            className={workspace === "propagate" ? "is-active" : ""}
            onClick={() => changeWorkspace("propagate")}
          >
            Propagate
          </button>}
          {allowedWorkspaces.includes("review") && <button
            type="button"
            className={workspace === "review" ? "is-active" : ""}
            onClick={() => changeWorkspace("review")}
          >
            Review QA
          </button>}
          {allowedWorkspaces.includes("catalog") && <button
            type="button"
            className={workspace === "catalog" ? "is-active" : ""}
            onClick={() => changeWorkspace("catalog")}
          >
            SKU catalog
          </button>}
          {allowedWorkspaces.includes("analytics") && <button
            type="button"
            className={workspace === "analytics" ? "is-active" : ""}
            onClick={() => changeWorkspace("analytics")}
          >
            Analytics
          </button>}
            </>
          )}
        </nav>
        <div className="topbar-status">
          {currentUser && (
            <span className="session-user">
              <strong>{currentUser.username}</strong>
              <span className={`role-chip ${currentUser.role}`}>{currentUser.role}</span>
            </span>
          )}
          {fixtureError && (
            <span className="fixture-error">Image workspace unavailable</span>
          )}
          <span className={`api-status ${apiStatus}`}>API {apiStatus}</span>
          {gapReview ? (
            <span>
              {liveAnnotations.filter(
                (box) =>
                  box.classType === "gap" &&
                  box.lifecycleState === "verified" &&
                  box.reviewState === "accepted",
              ).length}
              /{liveAnnotations.filter((box) => box.lifecycleState !== "rejected").length} gaps accepted
            </span>
          ) : workspace === "verify" ? (
            <>
              <span>
                {progress.verified}/{progress.total} verified
              </span>
              <span
                className="progress-track"
                aria-label={`${progress.percent}% verified`}
              >
                <span style={{ width: `${progress.percent}%` }} />
              </span>
            </>
          ) : workspace === "assign" ? (
            <span>
              {assignmentProgress.assigned}/{assignmentProgress.total} assigned
            </span>
          ) : workspace === "propagate" ? (
            <span>Human confirmation required</span>
          ) : workspace === "review" ? (
            <span>Blocking risks must be cleared</span>
          ) : workspace === "analytics" ? (
            <span>Verified snapshot metrics</span>
          ) : (
            <span>Catalog management</span>
          )}
          {onLogout && (
            <button className="logout-button" type="button" onClick={() => void onLogout()}>
              Sign out
            </button>
          )}
        </div>
      </header>
      {gapReview && (workspace === "verify" || workspace === "assign") && (
        <GapReviewNavigator
          manifest={gapReview}
          index={gapReviewIndex}
          busy={reviewBusy}
          reviewed={imageReviewed}
          unresolvedCount={unresolvedCount}
          saveStatus={saveStatus}
          error={reviewError}
          onSelect={(index) => void selectGapReviewImage(index)}
          onMarkReviewed={() => void markCurrentImageReviewed()}
        />
      )}
      {!gapReview && reviewImageLoaded && (workspace === "verify" || workspace === "assign") && (
        <ImageReviewBar
          busy={reviewBusy}
          reviewed={imageReviewed}
          unresolvedCount={unresolvedCount}
          saveStatus={saveStatus}
          error={reviewError}
          place={neighbors}
          onPrevious={
            neighbors?.previousId ? () => void openNeighbor(neighbors.previousId!) : undefined
          }
          onNext={neighbors?.nextId ? () => void openNeighbor(neighbors.nextId!) : undefined}
          onMarkReviewed={() => void markCurrentImageReviewed(true)}
        />
      )}
      {workspace === "verify" || workspace === "assign" ? (
        <AnnotationWorkspace
          fixture={fixture}
          mode={workspace}
          purpose={gapReview ? "gap-review" : "annotation"}
          onAnnotationsChange={handleAnnotationsChange}
          onSaveStateChange={reviewImageId ? handleSaveStateChange : undefined}
          key={`${fixture.images.map((image) => image.id).join(",")}:${workspace}`}
        />
      ) : workspace === "propagate" ? (
        <PropagationWorkspace
          fixture={{ ...fixture, annotations: liveAnnotations }}
          annotations={liveAnnotations}
          onAnnotationsChange={handleAnnotationsChange}
        />
      ) : workspace === "review" ? (
        <ReviewWorkspace datasetVersionId={datasetVersionId} />
      ) : workspace === "analytics" ? (
        <AnalyticsWorkspace datasetVersionId={datasetVersionId} />
      ) : (
        <CatalogWorkspace />
      )}
    </div>
  );
}
