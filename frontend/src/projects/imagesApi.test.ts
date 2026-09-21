import { describe, expect, it, vi } from "vitest";

import {
  loadProjectImages,
  ProjectImageApiError,
  uploadProjectImage,
} from "./imagesApi";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";

describe("project image API", () => {
  it("loads a bounded filtered image page", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        images: [imageResponse()],
        total: 1,
        limit: 60,
        offset: 0,
      }),
    });

    const page = await loadProjectImages(
      PROJECT_ID,
      VERSION_ID,
      "reviewed",
      0,
      60,
      fetcher,
    );

    expect(page.images[0]).toMatchObject({
      id: IMAGE_ID,
      originalFilename: "shelf.jpg",
      status: "reviewed",
      thumbnailUrl: `/api/images/${IMAGE_ID}/media/thumbnail`,
    });
    expect(fetcher).toHaveBeenCalledWith(
      `/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/images` +
        "?limit=60&offset=0&status=reviewed",
      { headers: { Accept: "application/json" } },
    );
  });

  it("rejects unsafe image response URLs", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        images: [{ ...imageResponse(), thumbnail_url: "https://example.com/private.jpg" }],
        total: 1,
        limit: 60,
        offset: 0,
      }),
    });

    await expect(
      loadProjectImages(PROJECT_ID, VERSION_ID, null, 0, 60, fetcher),
    ).rejects.toEqual(new ProjectImageApiError(502, "invalid_response"));
  });

  it("uploads one image as multipart form data", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: IMAGE_ID }),
    });
    const file = new File(["image"], "shelf.jpg", { type: "image/jpeg" });

    await uploadProjectImage(PROJECT_ID, VERSION_ID, file, fetcher);

    const request = fetcher.mock.calls[0]!;
    expect(request[0]).toBe(
      `/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/images`,
    );
    expect(request[1]).toMatchObject({ method: "POST" });
    expect((request[1]!.body as FormData).get("file")).toBe(file);
  });

  it("preserves duplicate-image errors", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "duplicate_image" } }),
    });

    await expect(
      uploadProjectImage(
        PROJECT_ID,
        VERSION_ID,
        new File(["image"], "shelf.jpg", { type: "image/jpeg" }),
        fetcher,
      ),
    ).rejects.toEqual(new ProjectImageApiError(409, "duplicate_image"));
  });
});

function imageResponse() {
  return {
    id: IMAGE_ID,
    original_filename: "shelf.jpg",
    media_type: "image/jpeg",
    width: 1200,
    height: 800,
    status: "reviewed",
    created_at: "2026-08-30T08:00:00Z",
    thumbnail_url: `/api/images/${IMAGE_ID}/media/thumbnail`,
  };
}
