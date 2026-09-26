import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  localStorage.clear();
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("App health", () => {
  it("renders the annotation shell and reports a healthy API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "shelfsight-api" }),
      }),
    );

    render(<App />);

    expect(document.querySelector(".brand-mark")).toHaveAttribute(
      "src",
      "/cvsight-mark.svg",
    );
    expect(screen.getByText("Verify boxes")).toBeInTheDocument();
    expect(screen.getByLabelText("Shelf annotation canvas")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("API online")).toBeInTheDocument());
  });

  it("reports an unavailable API without exposing an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private network error")));

    render(<App />);

    await waitFor(() => expect(screen.getByText("API unavailable")).toBeInTheDocument());
    expect(screen.queryByText("private network error")).not.toBeInTheDocument();
  });

  it("rejects an unexpected health response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "other-service" }),
      }),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByText("API unavailable")).toBeInTheDocument());
  });

  it("switches between verification and the SKU catalog", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        if (String(input) === "/api/health") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: "ok", service: "shelfsight-api" }),
          });
        }
        return Promise.reject(new TypeError("catalog unavailable"));
      }),
    );
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "SKU catalog" }));
    expect(screen.getByLabelText("SKU catalog search")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "API unavailable. Showing the read-only 2,001 SKU fixture",
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Verify boxes" }));
    expect(screen.getByLabelText("Shelf annotation canvas")).toBeInTheDocument();
  });

  it("opens the explicit SKU assignment workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        if (String(input) === "/api/health") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: "ok", service: "shelfsight-api" }),
          });
        }
        return Promise.reject(new TypeError("catalog unavailable"));
      }),
    );
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Assign SKUs" }));

    expect(await screen.findByLabelText("SKU assignment search")).toBeInTheDocument();
    expect(screen.getByText("0/225 assigned")).toBeInTheDocument();
    expect(screen.getByLabelText("Close SKU picker")).toBeInTheDocument();
    expect(document.querySelector(".sku-picker-popover")).toBeInTheDocument();
  });

  it("opens the quality review workspace", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "shelfsight-api" }),
      }),
    );
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Review QA" }));

    expect(screen.getByText("Open a dataset review")).toBeInTheDocument();
    expect(screen.getByText("Blocking risks must be cleared")).toBeInTheDocument();
  });

  it("opens the analytics workspace", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "shelfsight-api" }),
      }),
    );
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Analytics" }));

    expect(screen.getByText("Open an immutable snapshot")).toBeInTheDocument();
    expect(screen.getByText("Verified snapshot metrics")).toBeInTheDocument();
  });

  it("opens quality review directly from a dataset version URL", () => {
    window.history.replaceState(
      {},
      "",
      "/?version=11111111-1111-4111-8111-111111111111",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "shelfsight-api" }),
      }),
    );

    render(<App />);

    expect(screen.getByText("Quality review")).toBeInTheDocument();
    expect(screen.queryByLabelText("Shelf annotation canvas")).not.toBeInTheDocument();
  });

  it("opens a live database image from the image query parameter", async () => {
    const imageId = "11111111-1111-4111-8111-111111111111";
    const annotationId = "22222222-2222-4222-8222-222222222222";
    window.history.replaceState({}, "", `/?image=${imageId}`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/health") {
          return Promise.resolve({
            ok: true,
            json: async () => ({ status: "ok", service: "shelfsight-api" }),
          });
        }
        if (url === `/api/images/${imageId}`) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              id: imageId,
              original_filename: "live-shelf.jpg",
              width: 100,
              height: 80,
              canonical_url: `/api/images/${imageId}/media/canonical`,
            }),
          });
        }
        if (url === `/api/images/${imageId}/annotations`) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              annotations: [
                {
                  id: annotationId,
                  image_id: imageId,
                  revision: 1,
                  x: 10,
                  y: 10,
                  width: 20,
                  height: 30,
                  class_type: "product",
                  lifecycle_state: "proposed",
                  review_state: "unreviewed",
                  source: "model",
                  sku_id: null,
                  confidence: 0.8,
                  occluded: false,
                  truncated: false,
                  shelf_row: 0,
                },
              ],
            }),
          });
        }
        return Promise.reject(new Error(`unexpected request: ${url}`));
      }),
    );

    render(<App />);

    expect(await screen.findByText("live-shelf.jpg")).toBeInTheDocument();
    expect(screen.getByText("0/1 verified")).toBeInTheDocument();
    expect(screen.queryByText("Image workspace unavailable")).not.toBeInTheDocument();
  });

  it("marks a routed project image reviewed once every box is decided", async () => {
    const imageId = "11111111-1111-4111-8111-111111111111";
    const fetcher = liveImageFetch(imageId, "verified", "accepted");
    vi.stubGlobal("fetch", fetcher);

    render(<App imageId={imageId} />);

    const button = await screen.findByRole("button", { name: "Mark reviewed" });
    fireEvent.click(button);

    expect(
      await screen.findByRole("button", { name: "Image reviewed" }),
    ).toBeDisabled();
    expect(fetcher).toHaveBeenCalledWith(
      `/api/images/${imageId}/reviewed`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("keeps review blocked while a routed image has undecided boxes", async () => {
    const imageId = "11111111-1111-4111-8111-111111111111";
    vi.stubGlobal("fetch", liveImageFetch(imageId, "proposed", "unreviewed"));

    render(<App imageId={imageId} />);

    expect(
      await screen.findByRole("button", { name: "1 decisions remaining" }),
    ).toBeDisabled();
  });

  it("moves between project photos and marks one reviewed before the next", async () => {
    const imageId = "11111111-1111-4111-8111-111111111111";
    const previous = "33333333-3333-4333-8333-333333333333";
    const next = "44444444-4444-4444-8444-444444444444";
    const datasetId = "55555555-5555-4555-8555-555555555555";
    const versionId = "66666666-6666-4666-8666-666666666666";
    const fetcher = liveImageFetch(imageId, "verified", "accepted", {
      previous_id: previous,
      next_id: next,
      position: 2,
      total: 3,
    });
    vi.stubGlobal("fetch", fetcher);
    const onOpenImage = vi.fn();

    render(
      <App
        imageId={imageId}
        datasetId={datasetId}
        datasetVersionId={versionId}
        initialWorkspace="verify"
        onOpenImage={onOpenImage}
      />,
    );

    expect(await screen.findByLabelText("Photo position")).toHaveTextContent("2 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await waitFor(() => expect(onOpenImage).toHaveBeenCalledWith(previous));

    fireEvent.click(await screen.findByRole("button", { name: "Mark reviewed and next" }));
    await waitFor(() => expect(onOpenImage).toHaveBeenLastCalledWith(next));
    expect(fetcher).toHaveBeenCalledWith(
      `/api/images/${imageId}/reviewed`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("shows only workspaces allowed for an annotator", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok", service: "shelfsight-api" }),
      }),
    );

    render(<App currentUser={{ username: "labeler", role: "annotator" }} />);

    expect(screen.getByRole("button", { name: "Verify boxes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Assign SKUs" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Propagate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review QA" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "SKU catalog" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analytics" })).not.toBeInTheDocument();
  });
});

function liveImageFetch(
  imageId: string,
  lifecycleState: string,
  reviewState: string,
  neighbors: Record<string, unknown> | null = null,
) {
  let reviewed = false;
  return vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: async () => body });
    if (url === "/api/health") {
      return ok({ status: "ok", service: "shelfsight-api" });
    }
    if (neighbors && url.endsWith(`/images/${imageId}/neighbors`)) {
      return ok(neighbors);
    }
    if (url === `/api/images/${imageId}/reviewed` && init?.method === "POST") {
      reviewed = true;
      return ok({ id: imageId, status: "reviewed" });
    }
    if (url === `/api/images/${imageId}`) {
      return ok({
        id: imageId,
        original_filename: "pilot-shelf.jpg",
        width: 100,
        height: 80,
        status: reviewed ? "reviewed" : "in_progress",
        canonical_url: `/api/images/${imageId}/media/canonical`,
      });
    }
    if (url === `/api/images/${imageId}/annotations`) {
      return ok({
        annotations: [
          {
            id: "22222222-2222-4222-8222-222222222222",
            image_id: imageId,
            revision: 1,
            x: 10,
            y: 10,
            width: 20,
            height: 30,
            class_type: "product",
            lifecycle_state: lifecycleState,
            review_state: reviewState,
            source: "model",
            sku_id: null,
            confidence: 0.8,
            occluded: false,
            truncated: false,
            shelf_row: 0,
          },
        ],
      });
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
}
