import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectRouter } from "./ProjectRouter";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const PROJECT = {
  id: PROJECT_ID,
  name: "Retail Q3 audit",
  description: "Dhaka stores",
  created_at: "2026-08-30T08:00:00Z",
  open_version_id: VERSION_ID,
  latest_version_id: VERSION_ID,
  image_count: 24,
};

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ProjectRouter", () => {
  it("shows the project dashboard after sign in", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [PROJECT] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Your projects" })).toBeInTheDocument();
    expect(await screen.findByText("Retail Q3 audit")).toBeInTheDocument();
    expect(screen.getByText("Dhaka stores")).toBeInTheDocument();
    expect(screen.getByText("24 shelf images")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New project" })).toBeInTheDocument();
  });

  it("creates a project and opens its first-image checklist", async () => {
    let projectCreated = false;
    let createBody: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          createBody = JSON.parse(String(init.body)) as Record<string, unknown>;
          projectCreated = true;
          return Promise.resolve(response(201, {
            id: PROJECT_ID,
            open_version_id: VERSION_ID,
            imported_skus: 2,
          }));
        }
        if (String(_input) === `/api/dataset-versions/${VERSION_ID}/progress`) {
          return Promise.resolve(response(200, emptyProgressResponse()));
        }
        return Promise.resolve({
          ok: true,
          json: async () => (projectCreated ? [PROJECT] : []),
        });
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    await screen.findByText("Create your first project");
    fireEvent.click(screen.getAllByRole("button", { name: "New project" })[0]!);
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Retail Q3 audit" },
    });
    fireEvent.change(screen.getByLabelText(/Description/), {
      target: { value: "Dhaka stores" },
    });
    fireEvent.change(screen.getByLabelText(/SKU catalog CSV/), {
      target: {
        files: [new File([
          "name,upc,brand\nAurora Cola,012345678905,Aurora\nNorthstar Water,,Northstar",
        ], "catalog.csv", { type: "text/csv" })],
      },
    });
    expect(await screen.findByText("2 SKUs ready to import")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Retail Q3 audit")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "First image checklist" })).toBeInTheDocument();
    expect(screen.getByText("1 of 3 steps complete")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload first image" })).toBeInTheDocument();
    expect(createBody).toMatchObject({
      name: "Retail Q3 audit",
      catalog: [
        { name: "Aurora Cola", upc: "012345678905", brand: "Aurora" },
        { name: "Northstar Water", upc: null, brand: "Northstar" },
      ],
    });
    expect(window.location.pathname).toBe(`/projects/${PROJECT_ID}`);

    fireEvent.click(screen.getByRole("button", { name: "Upload first image" }));
    await waitFor(() => expect(window.location.pathname).toBe(
      `/projects/${PROJECT_ID}/images`,
    ));
  });

  it("shows duplicate-name feedback without leaking API details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === "POST") {
          return Promise.resolve({
            ok: false,
            status: 409,
            json: async () => ({ detail: { code: "dataset_name_conflict" } }),
          });
        }
        return Promise.resolve({ ok: true, json: async () => [] });
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );
    await screen.findByText("Create your first project");
    fireEvent.click(screen.getAllByRole("button", { name: "New project" })[0]!);
    fireEvent.change(screen.getByLabelText("Project name"), {
      target: { value: "Existing" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(
      await screen.findByText("A project with this name already exists."),
    ).toBeInTheDocument();
  });

  it("does not show project creation to an annotator", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [PROJECT] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "labeler", role: "annotator" }}
        onLogout={vi.fn()}
      />,
    );

    await screen.findByText("Retail Q3 audit");
    expect(screen.queryByRole("button", { name: "New project" })).not.toBeInTheDocument();
  });

  it("does not expose the creation form through an annotator deep link", async () => {
    window.history.replaceState({}, "", "/projects/new");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [PROJECT] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "labeler", role: "annotator" }}
        onLogout={vi.fn()}
      />,
    );

    await screen.findByText("Retail Q3 audit");
    expect(screen.queryByRole("heading", { name: "Create a project" })).not.toBeInTheDocument();
  });

  it("opens an existing project overview and its review workspace", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        if (String(input) === "/api/datasets") {
          return Promise.resolve({ ok: true, json: async () => [PROJECT] });
        }
        if (String(input) === `/api/dataset-versions/${VERSION_ID}/review-queue`) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              dataset_version_id: VERSION_ID,
              status: "open",
              total_risk_items: 0,
              unresolved_count: 0,
              resolved_count: 0,
              items: [],
              signoff: null,
            }),
          });
        }
        if (String(input) === `/api/dataset-versions/${VERSION_ID}/progress`) {
          return Promise.resolve({
            ok: true,
            json: async () => progressResponse(),
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: "ok", service: "shelfsight-api" }),
        });
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open project" }));

    expect(await screen.findByText("Project overview")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Retail Q3 audit" })).toBeInTheDocument();
    expect(screen.getAllByText("Working version")).toHaveLength(2);
    expect(screen.getByText("What happens next")).toBeInTheDocument();
    expect(await screen.findByText("Box decisions")).toBeInTheDocument();
    expect(screen.getByText("15/20")).toBeInTheDocument();
    expect(screen.getByText("9 known · 2 Unknown · 1 unassigned")).toBeInTheDocument();
    expect(screen.getByText("1 flag remains")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "First image checklist" })).not.toBeInTheDocument();
    expect(window.location.pathname).toBe(`/projects/${PROJECT_ID}`);

    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(await screen.findByText("Quality review")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("API online")).toBeInTheDocument());
    expect(window.location.pathname).toBe(`/projects/${PROJECT_ID}/review`);
    expect(window.location.search).toBe("");
  });

  it("opens a bookmarked review route after a page load", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/review`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        if (String(input) === "/api/datasets") {
          return Promise.resolve({ ok: true, json: async () => [PROJECT] });
        }
        if (String(input) === `/api/dataset-versions/${VERSION_ID}/review-queue`) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              dataset_version_id: VERSION_ID,
              status: "open",
              total_risk_items: 0,
              unresolved_count: 0,
              resolved_count: 0,
              items: [],
              signoff: null,
            }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: "ok", service: "shelfsight-api" }),
        });
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "reviewer", role: "reviewer" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByText("Quality review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Retail Q3 audit$/ })).toBeInTheDocument();
    expect(screen.queryByText(/dense-shelf-demonstration/)).not.toBeInTheDocument();
    expect(window.location.pathname).toBe(`/projects/${PROJECT_ID}/review`);
  });

  it("does not expose owner-only workspaces to an annotator", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/catalog`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [PROJECT] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "labeler", role: "annotator" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByText("Project overview")).toBeInTheDocument();
    expect(screen.queryByText("SKU catalog management")).not.toBeInTheDocument();
  });

  it("shows a safe not-found state for an absent project", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Project not found" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to projects" })).toBeInTheDocument();
  });

  it("keeps the overview usable and retries unavailable progress", async () => {
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}`);
    let progressAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        if (String(input) === "/api/datasets") {
          return Promise.resolve({ ok: true, json: async () => [PROJECT] });
        }
        progressAttempts += 1;
        return Promise.resolve(
          progressAttempts === 1
            ? { ok: false, status: 503, json: async () => ({}) }
            : { ok: true, json: async () => progressResponse() },
        );
      }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByText("Progress unavailable")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Retail Q3 audit" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry progress" }));
    expect(await screen.findByText("Box decisions")).toBeInTheDocument();
  });

  it("opens version management with review and immutable export status", async () => {
    const RELEASE_ID = "44444444-4444-4444-8444-444444444444";
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/datasets") {
          return Promise.resolve(response(200, [PROJECT]));
        }
        if (url === `/api/dataset-versions/${VERSION_ID}/progress`) {
          return Promise.resolve(response(200, progressResponse()));
        }
        if (url === `/api/datasets/${PROJECT_ID}/versions`) {
          return Promise.resolve(response(200, [
            versionResponse(VERSION_ID, "working", null, RELEASE_ID),
            versionResponse(RELEASE_ID, "released", "2026-08-31T08:00:00Z", null),
          ]));
        }
        if (url === `/api/dataset-versions/${VERSION_ID}/review-queue`) {
          return Promise.resolve(response(200, {
            total_risk_items: 12,
            unresolved_count: 2,
            resolved_count: 10,
          }));
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

    fireEvent.click(await screen.findByRole("button", { name: "Versions" }));
    expect(await screen.findByRole("heading", { name: "Versions and releases" })).toBeInTheDocument();
    expect(screen.getByText("2 unresolved")).toBeInTheDocument();
    expect(screen.getByText("10 of 12 risk items resolved.")).toBeInTheDocument();
    expect(screen.getByText(/reviewer:qa/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Detection ZIP/ })).toHaveAttribute(
      "href",
      `/api/dataset-versions/${RELEASE_ID}/exports/detection`,
    );
    expect(window.location.pathname).toBe(`/projects/${PROJECT_ID}/versions`);
  });

  it("starts the next working version from an immutable release", async () => {
    const RELEASE_ID = "44444444-4444-4444-8444-444444444444";
    const frozenProject = {
      ...PROJECT,
      open_version_id: null,
      latest_version_id: RELEASE_ID,
    };
    let created = false;
    window.history.replaceState({}, "", `/projects/${PROJECT_ID}/versions`);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/datasets") {
          return Promise.resolve(response(200, [created ? PROJECT : frozenProject]));
        }
        if (url === `/api/datasets/${PROJECT_ID}/versions` && init?.method === "POST") {
          created = true;
          return Promise.resolve(response(201, { id: VERSION_ID }));
        }
        if (url === `/api/datasets/${PROJECT_ID}/versions`) {
          return Promise.resolve(response(200, created
            ? [
                versionResponse(VERSION_ID, "working", null, RELEASE_ID),
                versionResponse(RELEASE_ID, "released", "2026-08-31T08:00:00Z", null),
              ]
            : [versionResponse(RELEASE_ID, "released", "2026-08-31T08:00:00Z", null)]));
        }
        if (url === `/api/dataset-versions/${VERSION_ID}/review-queue`) {
          return Promise.resolve(response(200, {
            total_risk_items: 0,
            unresolved_count: 0,
            resolved_count: 0,
          }));
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

    fireEvent.click(await screen.findByRole("button", { name: "Start next working version" }));
    expect(await screen.findByText("Ready for sign-off")).toBeInTheDocument();
    expect(screen.getByText("Working version open")).toBeInTheDocument();
  });

  it("handles a malformed project route without crashing", async () => {
    window.history.replaceState({}, "", "/projects/%ZZ/review");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
    );

    render(
      <ProjectRouter
        currentUser={{ username: "owner", role: "owner" }}
        onLogout={vi.fn()}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Your projects" })).toBeInTheDocument();
  });
});

function progressResponse() {
  return {
    dataset_version_id: VERSION_ID,
    images: { total: 10 },
    annotations: { total: 20, decided: 15 },
    identity: {
      accepted_products: 12,
      known_products: 9,
      unknown_products: 2,
      unassigned_products: 1,
    },
    qa: { reviewed_images: 4, flagged_annotations: 1 },
  };
}

function emptyProgressResponse() {
  return {
    dataset_version_id: VERSION_ID,
    images: { total: 0 },
    annotations: { total: 0, decided: 0 },
    identity: {
      accepted_products: 0,
      known_products: 0,
      unknown_products: 0,
      unassigned_products: 0,
    },
    qa: { reviewed_images: 0, flagged_annotations: 0 },
  };
}

function versionResponse(
  id: string,
  status: "working" | "released",
  snapshotAt: string | null,
  parentVersionId: string | null,
) {
  return {
    id,
    dataset_id: PROJECT_ID,
    parent_version_id: parentVersionId,
    created_at: status === "working" ? "2026-09-01T08:00:00Z" : "2026-08-30T08:00:00Z",
    snapshot_at: snapshotAt,
    status,
    image_count: 24,
    review_signoff: status === "released" ? {
      signed_by: "reviewer:qa",
      signed_at: "2026-08-31T08:00:00Z",
      reviewed_annotation_count: 180,
      risk_item_count: 12,
    } : null,
    export_types: status === "released" ? ["detection"] : [],
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
