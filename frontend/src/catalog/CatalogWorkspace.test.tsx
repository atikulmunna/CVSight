import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CatalogWorkspace } from "./CatalogWorkspace";

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("CatalogWorkspace", () => {
  it("keeps the 2,001 SKU fixture virtualized and finds a hard variant", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<CatalogWorkspace />);

    expect(
      await screen.findByText(
        "API unavailable. Showing the read-only 2,001 SKU fixture",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("option").length).toBeLessThan(50);

    fireEvent.change(screen.getByPlaceholderText("Try aur zro 330"), {
      target: { value: "aur cla 250 1" },
    });

    expect(screen.getAllByRole("option").length).toBeGreaterThan(0);
    expect(screen.getAllByRole("option")[0]).toHaveTextContent(
      "Aurora Cola 250ml 1 pack",
    );
  });

  it("stores favorites and recents without rendering a flat dropdown", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<CatalogWorkspace />);
    await screen.findByText(
      "API unavailable. Showing the read-only 2,001 SKU fixture",
    );

    fireEvent.change(screen.getByPlaceholderText("Try aur zro 330"), {
      target: { value: "Aurora Cola 250ml 1 pack" },
    });
    fireEvent.click(screen.getAllByRole("option")[0]!);
    fireEvent.click(screen.getByRole("button", { name: "Add favorite" }));
    fireEvent.change(screen.getByPlaceholderText("Try aur zro 330"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "favorites" }));

    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getAllByRole("option")[0]).toHaveTextContent(
      "Aurora Cola 250ml 1 pack",
    );
    expect(localStorage.getItem("shelfsight:sku-favorites")).toContain(
      "demo-sku-0001",
    );

    fireEvent.click(screen.getByRole("button", { name: "recent" }));
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("creates a SKU through the live catalog API", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response(200, {
          skus: [serverSku("unknown", "Unknown / Other", true)],
          total: 1,
          limit: 2500,
          offset: 0,
        }),
      )
      .mockResolvedValueOnce(
        response(
          201,
          serverSku("new-sku", "Aurora Cola Zero 330ml", false, {
            upc: "012345678905",
            brand: "Aurora",
            variant: "Zero 330ml",
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(<CatalogWorkspace />);
    await screen.findByText("1 SKUs loaded");

    fireEvent.click(screen.getByRole("button", { name: "New SKU" }));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Aurora Cola Zero 330ml" },
    });
    fireEvent.change(screen.getByLabelText("upc"), {
      target: { value: "012345678905" },
    });
    fireEvent.change(screen.getByLabelText("brand"), {
      target: { value: "Aurora" },
    });
    fireEvent.change(screen.getByLabelText("variant"), {
      target: { value: "Zero 330ml" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create SKU" }));

    expect(await screen.findByText("SKU created")).toBeInTheDocument();
    expect(
      screen.getByText("Aurora Cola Zero 330ml", {
        selector: ".catalog-detail-header strong",
      }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("merges through searched targets and uploads a reference image", async () => {
    const source = serverSku("source", "Aurora Cola duplicate");
    const target = serverSku("target", "Aurora Cola canonical");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response(200, {
          skus: [source, target],
          total: 2,
          limit: 2500,
          offset: 0,
        }),
      )
      .mockResolvedValueOnce(
        response(201, {
          id: "reference-1",
          sku_id: "source",
          original_filename: "front.png",
          thumbnail_url: "/api/reference-1/thumbnail",
        }),
      )
      .mockResolvedValueOnce(
        response(200, {
          source: {
            ...source,
            status: "merged",
            merged_into_id: "target",
          },
          target,
          repointed_annotations: 7,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(<CatalogWorkspace />);
    await screen.findByText("2 SKUs loaded");

    const upload = screen.getByLabelText("Add validated image");
    fireEvent.change(upload, {
      target: {
        files: [new File(["image"], "front.png", { type: "image/png" })],
      },
    });
    expect(await screen.findByText("Reference image added")).toBeInTheDocument();
    expect(screen.getByText("1 images")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search target SKU"), {
      target: { value: "canonical" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Aurora Cola canonical/ }),
    );

    expect(
      await screen.findByText("Merged SKU and repointed 7 annotations"),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

function serverSku(
  id: string,
  name: string,
  isUnknown = false,
  overrides: Record<string, unknown> = {},
) {
  return {
    id,
    name,
    upc: null,
    category: "Beverages",
    subcategory: "Soft drinks",
    brand: "Aurora",
    variant: null,
    is_unknown: isUnknown,
    status: "active",
    merged_into_id: null,
    reference_images: [],
    ...overrides,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}
