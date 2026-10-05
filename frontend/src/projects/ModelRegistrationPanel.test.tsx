import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModelRegistrationPanel } from "./ModelRegistrationPanel";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const RELEASE_ID = "22222222-2222-4222-8222-222222222222";
const WORKING_ID = "33333333-3333-4333-8333-333333333333";
const JOB_ID = "44444444-4444-4444-8444-444444444444";
const ENTRY_ID = "55555555-5555-4555-8555-555555555555";
const STORAGE_KEY = `cvsight-model-evaluation:${PROJECT_ID}`;

function response(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}

function version(id: string, status: "working" | "released") {
  return {
    id,
    dataset_id: PROJECT_ID,
    parent_version_id: null,
    created_at: "2026-09-20T08:00:00Z",
    snapshot_at: status === "released" ? "2026-09-21T08:00:00Z" : null,
    status,
    image_count: 42,
    review_signoff:
      status === "released"
        ? {
            signed_by: "reviewer:qa",
            signed_at: "2026-09-21T08:00:00Z",
            reviewed_annotation_count: 10,
            risk_item_count: 1,
            snapshot_content_sha256: "d".repeat(64),
          }
        : null,
    export_types: [],
  };
}

function job(state: string, progress: number, result: unknown = null) {
  return {
    id: JOB_ID,
    job_type: "evaluate_detector",
    state,
    progress_current: progress,
    progress_total: 42,
    error_code: state === "failed" ? "model_runtime_unavailable" : null,
    result,
  };
}

const REPORT = {
  model_id: "qpds-seg-cls",
  model_version: "seg-afb9105f2007",
  model_artifact_sha256: "9".repeat(64),
  photos: 42,
  ground_truth_boxes: 1439,
  predicted_boxes: 1450,
  confidence_threshold: 0.3,
  metrics: {
    map_50: 0.98,
    map_50_95: 0.71,
    product_recall_at_iou_50: 0.997,
    precision_at_iou_50: 0.965,
    duplicate_rate_at_iou_50: 0.007,
    dense_scene_recall_at_iou_50: null,
    dense_scene_images: 0,
    overlapping_product_recall_at_iou_50: 0.75,
    overlapping_products: 8,
  },
};

function entry() {
  return {
    id: ENTRY_ID,
    model_role: "known_sku_detector",
    model_id: "qpds-seg-cls",
    model_version: "seg-afb9105f2007",
    model_artifact_id: ENTRY_ID,
    evaluation_artifact_id: ENTRY_ID,
    model_artifact_key: "models/qpds",
    evaluation_artifact_key: "evaluations/qpds.json",
    lineage: "external",
    source: "Trained by the retail CV team",
    training_dataset_version_id: null,
    evaluation_dataset_version_id: RELEASE_ID,
    model_artifact_sha256: "9".repeat(64),
    evaluation_artifact_sha256: "8".repeat(64),
    configuration: { confidence_threshold: 0.3 },
    compatibility: {},
    metrics: REPORT.metrics,
    registered_by: "owner:owner",
    registered_at: "2026-10-05T08:00:00Z",
    deployment_status: "candidate",
  };
}

function stubFetch(handlers: Record<string, (init?: RequestInit) => Promise<unknown>>) {
  const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const handler = handlers[`${init?.method ?? "GET"} ${url}`];
    return handler ? handler(init) : response(404, { detail: { code: "not_found" } });
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

describe("model registration panel", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("evaluates a served model on a release, shows its scores, and registers it", async () => {
    const polls = [job("running", 12), job("succeeded", 42, REPORT)];
    const fetcher = stubFetch({
      [`GET /api/datasets/${PROJECT_ID}/versions`]: () =>
        response(200, [version(WORKING_ID, "working"), version(RELEASE_ID, "released")]),
      "GET /api/model-registry/evaluations/defaults": () =>
        response(200, { runtime_url: "http://host.docker.internal:8091" }),
      "POST /api/model-registry/evaluations": () => response(202, job("queued", 0)),
      [`GET /api/jobs/${JOB_ID}`]: () => response(200, polls.shift()),
      [`POST /api/model-registry/evaluations/${JOB_ID}/register`]: () => response(201, entry()),
    });
    const onRegistered = vi.fn();
    render(<ModelRegistrationPanel projectId={PROJECT_ID} onRegistered={onRegistered} pollInterval={0} />);

    expect(await screen.findByDisplayValue("http://host.docker.internal:8091")).toBeInTheDocument();
    // Versions come newest first and are numbered oldest first; the working one is left out.
    expect(screen.getByRole("option", { name: "Version 1 (42 photos)" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Version 2/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run evaluation" }));

    expect(await screen.findByText("qpds-seg-cls seg-afb9105f2007")).toBeInTheDocument();
    expect(screen.getByText("99.7%")).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(JOB_ID);
    const register = screen.getByRole("button", { name: "Register model" });
    expect(register).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Where does this model come from?"), {
      target: { value: "Trained by the retail CV team" },
    });
    fireEvent.click(screen.getByLabelText("Its license allows us to use it here"));
    fireEvent.click(register);

    expect(await screen.findByText(/Registered qpds-seg-cls seg-afb9105f2007/)).toBeInTheDocument();
    expect(onRegistered).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    const started = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({
      dataset_version_id: RELEASE_ID,
      runtime_url: "http://host.docker.internal:8091",
      confidence_threshold: 0.3,
    });
  });

  it("explains that a model can be registered only once", async () => {
    window.localStorage.setItem(STORAGE_KEY, JOB_ID);
    stubFetch({
      [`GET /api/datasets/${PROJECT_ID}/versions`]: () => response(200, [version(RELEASE_ID, "released")]),
      "GET /api/model-registry/evaluations/defaults": () => response(200, { runtime_url: null }),
      [`GET /api/jobs/${JOB_ID}`]: () => response(200, job("succeeded", 42, REPORT)),
      [`POST /api/model-registry/evaluations/${JOB_ID}/register`]: () =>
        response(409, { detail: { code: "model_registration_conflict" } }),
    });
    render(<ModelRegistrationPanel projectId={PROJECT_ID} onRegistered={vi.fn()} pollInterval={0} />);

    // The stored evaluation is picked up again after a reload.
    fireEvent.change(await screen.findByLabelText("Where does this model come from?"), {
      target: { value: "Elsewhere" },
    });
    fireEvent.click(screen.getByLabelText("Its license allows us to use it here"));
    fireEvent.click(screen.getByRole("button", { name: "Register model" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This model is already registered");
  });

  it("says why an evaluation failed", async () => {
    window.localStorage.setItem(STORAGE_KEY, JOB_ID);
    stubFetch({
      [`GET /api/datasets/${PROJECT_ID}/versions`]: () => response(200, [version(RELEASE_ID, "released")]),
      "GET /api/model-registry/evaluations/defaults": () => response(200, { runtime_url: null }),
      [`GET /api/jobs/${JOB_ID}`]: () => response(200, job("failed", 3)),
    });
    render(<ModelRegistrationPanel projectId={PROJECT_ID} onRegistered={vi.fn()} pollInterval={0} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be reached at that address");
  });

  it("asks for a signed-off version when there is none", async () => {
    stubFetch({
      [`GET /api/datasets/${PROJECT_ID}/versions`]: () => response(200, [version(WORKING_ID, "working")]),
      "GET /api/model-registry/evaluations/defaults": () => response(200, { runtime_url: null }),
    });
    render(<ModelRegistrationPanel projectId={PROJECT_ID} onRegistered={vi.fn()} pollInterval={0} />);

    expect(await screen.findByText(/Sign off a version first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run evaluation" })).not.toBeInTheDocument();
  });
});
