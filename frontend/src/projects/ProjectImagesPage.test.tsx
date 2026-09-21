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
