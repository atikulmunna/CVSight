import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectRouter } from "./ProjectRouter";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";
const PROJECT = {
  id: PROJECT_ID,
  name: "Retail Q3 audit",
  description: "Dhaka stores",
  created_at: "2026-08-30T08:00:00Z",
  open_version_id: VERSION_ID,
  latest_version_id: VERSION_ID,
  image_count: 1,
};

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ProjectImagesPage", () => {
  it("shows server-backed images and filters by workflow status", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/datasets") {
        return Promise.resolve(response(200, [PROJECT]));
      }
      if (url === `/api/datasets/${PROJECT_ID}`) {
        return Promise.resolve(response(200, PROJECT));
      }
      if (url.includes("status=reviewed")) {
        return Promise.resolve(response(200, imagePage("reviewed")));
      }
      return Promise.resolve(response(200, imagePage("unlabeled")));
    });
    vi.stubGlobal("fetch", fetcher);

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByText("shelf-unlabeled.jpg")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload images" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reviewed" }));

    expect(await screen.findByText("shelf-reviewed.jpg")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(
      expect.stringContaining("status=reviewed"),
      expect.anything(),
    );
  });

  it("queues pre-labels for unlabeled photos and says when no worker is running", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/datasets") {
        return Promise.resolve(response(200, [PROJECT]));
      }
      if (url === `/api/datasets/${PROJECT_ID}`) {
        return Promise.resolve(response(200, PROJECT));
      }
      if (url === "/api/prelabels/batch" && init?.method === "POST") {
        return Promise.resolve(
          response(202, { items: [{ image_id: IMAGE_ID, status: "queued", job_state: "queued" }] }),
        );
      }
      if (url === "/api/workers/health") {
        return Promise.resolve(response(200, { status: "unavailable", workers: [] }));
      }
      return Promise.resolve(response(200, imagePage("unlabeled")));
    });
    vi.stubGlobal("fetch", fetcher);

    render(
      <ProjectRouter currentUser={{ username: "owner", role: "owner" }} onLogout={vi.fn()} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Pre-label unlabeled" }));

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Queued 1 photo for pre-labeling.");
    expect(notice).toHaveTextContent("No worker is running");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/prelabels/batch",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("hides pre-labeling from annotators", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/datasets") {
          return Promise.resolve(response(200, [PROJECT]));
        }
        if (url === `/api/datasets/${PROJECT_ID}`) {
          return Promise.resolve(response(200, PROJECT));
        }
        return Promise.resolve(response(200, imagePage("unlabeled")));
      }),
    );

    render(
      <ProjectRouter currentUser={{ username: "anna", role: "annotator" }} onLogout={vi.fn()} />,
    );

    expect(await screen.findByText("shelf-unlabeled.jpg")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pre-label unlabeled" })).not.toBeInTheDocument();
  });

  it("imports a labeled dataset after a preview, creating SKUs for new classes", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    const created = "44444444-4444-4444-8444-444444444444";
    const suggested = "55555555-5555-4555-8555-555555555555";
    const importBodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const base = `/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/labeled-imports`;
      if (url === "/api/datasets") {
        return Promise.resolve(response(200, [PROJECT]));
      }
      if (url === `/api/datasets/${PROJECT_ID}`) {
        return Promise.resolve(response(200, PROJECT));
      }
      if (url === `${base}/preview`) {
        return Promise.resolve(
          response(200, {
            format: "yolo",
            images: 2,
            boxes: 3,
            skipped_labels: 0,
            classes: [
              { name: "cola", boxes: 2, images: 2, suggested_sku_id: suggested },
              { name: "chips", boxes: 1, images: 1, suggested_sku_id: null },
            ],
            source_splits: { train: 2 },
          }),
        );
      }
      if (url === "/api/skus" && init?.method === "POST") {
        return Promise.resolve(
          response(201, {
            id: created,
            name: "chips",
            upc: null,
            category: null,
            subcategory: null,
            brand: null,
            variant: null,
            is_unknown: false,
            status: "active",
            merged_into_id: null,
            reference_images: [],
          }),
        );
      }
      if (url === base) {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        importBodies.push(body);
        const first = body.offset === 0;
        return Promise.resolve(
          response(200, {
            results: [
              {
                path: first ? "shelf/a.jpg" : "shelf/b.jpg",
                outcome: "imported",
                code: "image_imported",
                image_id: null,
                boxes: first ? 2 : 1,
              },
            ],
            next_offset: first ? 1 : null,
            total_images: 2,
          }),
        );
      }
      return Promise.resolve(response(200, imagePage("unlabeled")));
    });
    vi.stubGlobal("fetch", fetcher);

    render(
      <ProjectRouter currentUser={{ username: "owner", role: "owner" }} onLogout={vi.fn()} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Import labeled dataset" }));
    fireEvent.change(screen.getByLabelText("Path inside the import folder"), {
      target: { value: "shelf" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));

    expect(await screen.findByLabelText("SKU for cola")).toHaveValue("suggested");
    expect(screen.getByLabelText("SKU for chips")).toHaveValue("create");
    fireEvent.click(screen.getByRole("button", { name: "Import 2 images" }));

    expect(await screen.findByText("Imported 2 images with 3 boxes.")).toBeInTheDocument();
    expect(importBodies.map((body) => body.offset)).toEqual([0, 1]);
    expect(importBodies[0]!.class_skus).toEqual({ cola: suggested, chips: created });
    expect(importBodies[0]!.annotation_state).toBe("verified");
  });

  it("validates files and reports upload completion", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    let uploaded = false;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/datasets") {
          return Promise.resolve(response(200, [PROJECT]));
        }
        if (url === `/api/datasets/${PROJECT_ID}`) {
          return Promise.resolve(response(200, PROJECT));
        }
        if (init?.method === "POST") {
          uploaded = true;
          return Promise.resolve(response(201, { id: IMAGE_ID }));
        }
        return Promise.resolve(
          response(200, uploaded ? imagePage("unlabeled") : emptyImagePage()),
        );
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    await screen.findByText("No images yet");
    expect(screen.getByText("Step 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("Upload your first shelf image")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Upload images" })[0]!);
    const input = screen.getByLabelText(/Drop shelf photos here/) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [
          new File(["image"], "shelf.jpg", { type: "image/jpeg" }),
          new File(["text"], "notes.txt", { type: "text/plain" }),
        ],
      },
    });

    expect(screen.getByText("Use a JPEG or PNG image")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload 1" }));

    expect(await screen.findByText("Uploaded")).toBeInTheDocument();
    expect(await screen.findByText("shelf-unlabeled.jpg")).toBeInTheDocument();
    expect(screen.getByText("Step 3 of 3")).toBeInTheDocument();
    expect(screen.getByText("Open and review your first image")).toBeInTheDocument();
    expect(screen.getByText("2 of 2 processed")).toBeInTheDocument();
  });

  it("opens a selected image in the real annotation workspace", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/images`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/datasets") {
          return Promise.resolve(response(200, [PROJECT]));
        }
        if (url === `/api/datasets/${PROJECT_ID}`) {
          return Promise.resolve(response(200, PROJECT));
        }
        if (url.includes(`/datasets/${PROJECT_ID}/versions/${VERSION_ID}/images?`)) {
          return Promise.resolve(response(200, imagePage("unlabeled")));
        }
        if (url === "/api/health") {
          return Promise.resolve(response(200, { status: "ok", service: "shelfsight-api" }));
        }
        if (url === `/api/images/${IMAGE_ID}`) {
          return Promise.resolve(
            response(200, {
              id: IMAGE_ID,
              original_filename: "shelf-unlabeled.jpg",
              width: 1200,
              height: 800,
              canonical_url: `/api/images/${IMAGE_ID}/media/canonical`,
            }),
          );
        }
        if (url === `/api/images/${IMAGE_ID}/annotations`) {
          return Promise.resolve(response(200, { annotations: [] }));
        }
        if (url === "/api/skus?status=all&limit=2500") {
          return Promise.resolve(response(200, { skus: [] }));
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /shelf-unlabeled.jpg/ }));

    await waitFor(() => expect(window.location.pathname).toBe(
      `/projects/${PROJECT_ID}/annotate/${IMAGE_ID}`,
    ));
    expect(window.location.search).toBe("");
    expect(await screen.findByLabelText("Shelf annotation canvas")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("API online")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Assign SKUs" }));
    await waitFor(() => expect(window.location.pathname).toBe(
      `/projects/${PROJECT_ID}/annotate/${IMAGE_ID}/assign`,
    ));
  });
});

function imagePage(status: "unlabeled" | "reviewed") {
  return {
    images: [
      {
        id: IMAGE_ID,
        original_filename: `shelf-${status}.jpg`,
        media_type: "image/jpeg",
        width: 1200,
        height: 800,
        status,
        created_at: "2026-08-30T08:00:00Z",
        thumbnail_url: `/api/images/${IMAGE_ID}/media/thumbnail`,
      },
    ],
    total: 1,
    limit: 60,
    offset: 0,
  };
}

function emptyImagePage() {
  return { images: [], total: 0, limit: 60, offset: 0 };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
