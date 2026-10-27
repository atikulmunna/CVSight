import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectsPage } from "./ProjectsPage";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const RELEASED_PROJECT_ID = "33333333-3333-4333-8333-333333333333";
const RELEASE_ID = "44444444-4444-4444-8444-444444444444";
const IMAGE_IDS = [
  "55555555-5555-4555-8555-555555555555",
  "66666666-6666-4666-8666-666666666666",
];
const PROJECT = {
  id: PROJECT_ID,
  name: "Retail Q3 audit",
  description: "Dhaka stores",
  project_type: "object_detection",
  created_at: "2026-08-30T08:00:00Z",
  open_version_id: VERSION_ID,
  latest_version_id: VERSION_ID,
  image_count: 24,
};
const RELEASED_PROJECT = {
  id: RELEASED_PROJECT_ID,
  name: "Chittagong pilot",
  description: null,
  project_type: "instance_segmentation",
  created_at: "2026-07-01T08:00:00Z",
  open_version_id: null,
  latest_version_id: RELEASE_ID,
  image_count: 0,
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ProjectsPage", () => {
  it("lists each project with its photos, details and reviewed progress", async () => {
    const fetcher = stubApi((versionId) => Promise.resolve(response(
      200,
      versionId === VERSION_ID
        ? progressResponse(VERSION_ID, 10, 4)
        : progressResponse(RELEASE_ID, 0, 0),
    )));
    const onOpenProject = renderPage();

    expect(await screen.findByRole("table", { name: "2 projects" })).toBeInTheDocument();
    const row = screen.getByRole("row", { name: /Retail Q3 audit/ });
    expect(await within(row).findByText("4 of 10 reviewed")).toBeInTheDocument();
    expect(within(row).getByText("Dhaka stores")).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "Object detection" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "24" })).toBeInTheDocument();
    expect(within(row).getByText("Editable", { selector: "td" })).toBeInTheDocument();
    await waitFor(() => expect(
      [...row.querySelectorAll("img")].map((image) => image.getAttribute("src")),
    ).toEqual(IMAGE_IDS.map((id) => `/api/images/${id}/media/thumbnail`)));
    expect(fetcher).toHaveBeenCalledWith(
      `/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/images?limit=2&offset=0`,
      expect.anything(),
    );

    fireEvent.click(within(row).getByRole("button", { name: "Open Retail Q3 audit" }));
    expect(onOpenProject).toHaveBeenCalledWith(expect.objectContaining({ id: PROJECT_ID }));
  });

  it("reads progress from the latest release when no working version is open", async () => {
    const fetcher = stubApi((versionId) => Promise.resolve(
      versionId === RELEASE_ID
        ? response(200, progressResponse(RELEASE_ID, 0, 0))
        : response(200, progressResponse(VERSION_ID, 10, 4)),
    ));
    const onOpenProject = renderPage();

    const row = await screen.findByRole("row", { name: /Chittagong pilot/ });
    expect(await within(row).findByText("No images yet")).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "Instance segmentation" })).toBeInTheDocument();
    expect(within(row).getByRole("cell", { name: "None" })).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(
      `/api/dataset-versions/${RELEASE_ID}/progress`,
      expect.anything(),
    );
    const open = within(row).getByRole("button", { name: "Open Chittagong pilot" });
    expect(open).toBeDisabled();
    fireEvent.click(open);
    expect(onOpenProject).not.toHaveBeenCalled();
  });

  it("shows the list while progress is still loading", async () => {
    stubApi(() => new Promise(() => {}));
    renderPage();

    const row = await screen.findByRole("row", { name: /Retail Q3 audit/ });
    expect(within(row).getByText("Loading...")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Open Retail Q3 audit" })).toBeEnabled();
  });

  it("says progress is not available when it cannot be loaded", async () => {
    stubApi((versionId) => Promise.resolve(
      versionId === VERSION_ID
        ? response(503, {})
        : response(200, progressResponse(RELEASE_ID, 0, 0)),
    ));
    renderPage();

    const row = await screen.findByRole("row", { name: /Retail Q3 audit/ });
    expect(await within(row).findByText("Not available")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Open Retail Q3 audit" })).toBeEnabled();
    expect(
      await within(screen.getByRole("row", { name: /Chittagong pilot/ })).findByText("No images yet"),
    ).toBeInTheDocument();
  });

  it("filters by name or description without loading progress again", async () => {
    const fetcher = stubApi((versionId) => Promise.resolve(response(
      200,
      versionId === VERSION_ID
        ? progressResponse(VERSION_ID, 10, 4)
        : progressResponse(RELEASE_ID, 0, 0),
    )));
    renderPage();
    await screen.findByText("4 of 10 reviewed");
    const search = screen.getByRole("searchbox", { name: "Search projects" });

    fireEvent.change(search, { target: { value: "dhaka" } });
    expect(screen.getByRole("row", { name: /Retail Q3 audit/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Chittagong pilot/ })).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: "" } });
    expect(screen.getByRole("row", { name: /Chittagong pilot/ })).toBeInTheDocument();
    expect(screen.getByText("4 of 10 reviewed")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "sylhet" } });
    expect(screen.getByRole("heading", { name: "No matching projects" })).toBeInTheDocument();
    expect(progressRequests(fetcher)).toHaveLength(2);
  });
});

function renderPage() {
  const onOpenProject = vi.fn();
  render(
    <ProjectsPage
      currentUser={{ username: "owner", role: "owner" }}
      creating={false}
      onCancelCreate={vi.fn()}
      onCreated={vi.fn()}
      onNewProject={vi.fn()}
      onOpenProject={onOpenProject}
      onLogout={vi.fn()}
    />,
  );
  return onOpenProject;
}

function stubApi(progress: (versionId: string) => Promise<unknown>) {
  const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/api/datasets") {
      return Promise.resolve(response(200, [PROJECT, RELEASED_PROJECT]));
    }
    const progressMatch = /^\/api\/dataset-versions\/([^/]+)\/progress$/.exec(url);
    if (progressMatch) {
      return progress(progressMatch[1]!);
    }
    if (url.startsWith(`/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/images?`)) {
      return Promise.resolve(response(200, imagePage()));
    }
    return Promise.resolve(response(404, { detail: { code: "not_found" } }));
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

function progressRequests(fetcher: ReturnType<typeof vi.fn>) {
  return fetcher.mock.calls.filter(([input]) => String(input).endsWith("/progress"));
}

function progressResponse(versionId: string, total: number, reviewed: number) {
  return {
    dataset_version_id: versionId,
    images: { total },
    annotations: { total: 0, decided: 0 },
    identity: {
      accepted_products: 0,
      known_products: 0,
      unknown_products: 0,
      unassigned_products: 0,
    },
    qa: { reviewed_images: reviewed, flagged_annotations: 0 },
  };
}

function imagePage() {
  return {
    images: IMAGE_IDS.map((id, index) => ({
      id,
      original_filename: `shelf-${index + 1}.jpg`,
      media_type: "image/jpeg",
      width: 1200,
      height: 800,
      status: "unlabeled",
      created_at: "2026-08-30T08:00:00Z",
      thumbnail_url: `/api/images/${id}/media/thumbnail`,
    })),
    total: 24,
    limit: 2,
    offset: 0,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
