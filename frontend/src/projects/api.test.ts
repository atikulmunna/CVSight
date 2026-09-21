import { describe, expect, it, vi } from "vitest";

import { createProject, loadProjects, ProjectApiError } from "./api";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";

describe("project API", () => {
  it("loads and parses project summaries", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        {
          id: PROJECT_ID,
          name: "Retail Q3 audit",
          description: null,
          created_at: "2026-08-30T08:00:00Z",
          open_version_id: VERSION_ID,
          latest_version_id: VERSION_ID,
          image_count: 24,
        },
      ],
    });

    await expect(loadProjects(fetcher)).resolves.toEqual([
      {
        id: PROJECT_ID,
        name: "Retail Q3 audit",
        description: null,
        createdAt: "2026-08-30T08:00:00Z",
        openVersionId: VERSION_ID,
        latestVersionId: VERSION_ID,
        imageCount: 24,
      },
    ]);
    expect(fetcher).toHaveBeenCalledWith("/api/datasets", {
      headers: { Accept: "application/json" },
    });
  });

  it("rejects malformed project responses", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: "unsafe", name: "Broken" }],
    });

    await expect(loadProjects(fetcher)).rejects.toEqual(
      new ProjectApiError(502, "invalid_response"),
    );
  });

  it("creates a project with normalized fields", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        id: PROJECT_ID,
        open_version_id: VERSION_ID,
        imported_skus: 0,
      }),
    });

    await expect(
      createProject("  Retail Q3 audit  ", "   ", [], fetcher),
    ).resolves.toEqual({
      id: PROJECT_ID,
      openVersionId: VERSION_ID,
      importedSkus: 0,
    });

    expect(fetcher).toHaveBeenCalledWith("/api/datasets", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name: "Retail Q3 audit", description: null, catalog: [] }),
    });
  });

  it("rejects a malformed create response", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: PROJECT_ID }),
    });

    await expect(createProject("Retail", "", [], fetcher)).rejects.toEqual(
      new ProjectApiError(502, "invalid_response"),
    );
  });

  it("preserves a stable API error code", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: { code: "dataset_name_conflict" } }),
    });

    await expect(createProject("Existing", "", [], fetcher)).rejects.toEqual(
      new ProjectApiError(409, "dataset_name_conflict"),
    );
  });
});
