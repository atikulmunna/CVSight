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
      "/cvsight-mark.png",
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
