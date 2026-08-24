import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnnotationWorkspace } from "./AnnotationWorkspace";
import { DEMO_FIXTURE } from "./demoFixture";

afterEach(() => {
  localStorage.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("AnnotationWorkspace", () => {
  it("renders every overlay with a halo while virtualizing the list", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);

    expect(container.querySelectorAll(".annotation")).toHaveLength(320);
    expect(container.querySelectorAll(".overlay-halo")).toHaveLength(320);
    expect(container.querySelectorAll(".overlay-core")).toHaveLength(320);
    expect(container.querySelectorAll("[data-nonhue-cue]")).toHaveLength(320);
    expect(container.querySelectorAll(".overlay-secondary-halo")).toHaveLength(
      DEMO_FIXTURE.annotations.filter((box) => box.state === "propagated").length,
    );
    expect(container.querySelectorAll(".selection-handle")).toHaveLength(4);
    expect(screen.getAllByRole("option").length).toBeLessThan(40);
  });

  it("synchronizes canvas, list, details, and spatial keyboard navigation", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const secondOverlay = container.querySelector<SVGGElement>(
      '.annotation[data-annotation-id="B002"]',
    );
    expect(secondOverlay).not.toBeNull();

    fireEvent.pointerDown(secondOverlay!, { button: 0 });

    expect(secondOverlay).toHaveClass("is-selected");
    expect(
      container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("B002", { selector: ".detail-panel strong" })).toBeInTheDocument();

    const viewport = screen.getByLabelText("Shelf annotation canvas");
    fireEvent.keyDown(viewport, { key: "ArrowRight" });
    expect(
      container.querySelector('.annotation[data-annotation-id="B003"]'),
    ).toHaveClass("is-selected");

    const search = screen.getByPlaceholderText("SKU, class, state, or ID");
    fireEvent.keyDown(search, { key: "ArrowRight" });
    expect(
      container.querySelector('.annotation[data-annotation-id="B003"]'),
    ).toHaveClass("is-selected");
  });

  it("zooms the shared scene and clears focus with Escape", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const scene = container.querySelector<HTMLElement>(".canvas-scene");
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const originalTransform = scene?.style.transform;

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));

    expect(scene?.style.transform).not.toBe(originalTransform);
    expect(container.querySelector(".annotation-overlay")).toHaveClass("has-selection");

    fireEvent.keyDown(viewport, { key: "Escape" });

    expect(container.querySelector(".annotation-overlay")).not.toHaveClass("has-selection");
    expect(screen.getByText("Choose a box on the canvas or in the list.")).toBeInTheDocument();
  });

  it("pans the shared scene with Space and pointer movement", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const scene = container.querySelector<HTMLElement>(".canvas-scene");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });
    const originalTransform = scene?.style.transform;

    fireEvent.keyDown(viewport, { code: "Space", key: " " });
    fireEvent.pointerDown(viewport, {
      button: 0,
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(viewport, {
      pointerId: 1,
      clientX: 140,
      clientY: 130,
    });
    fireEvent.pointerUp(viewport, { pointerId: 1 });

    expect(scene?.style.transform).not.toBe(originalTransform);
  });

  it("toggles gap drawing from the toolbar and keyboard", () => {
    render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const control = screen.getByRole("button", { name: /Draw gap/ });

    fireEvent.click(control);
    expect(control).toHaveAttribute("aria-pressed", "true");
    expect(viewport).toHaveClass("is-drawing-gap");

    fireEvent.keyDown(viewport, { key: "g" });
    expect(control).toHaveAttribute("aria-pressed", "false");

    fireEvent.keyDown(viewport, { key: "g" });
    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(control).toHaveAttribute("aria-pressed", "false");
  });

  it("advances gap review to the next unresolved box after acceptance", () => {
    const annotations = DEMO_FIXTURE.annotations.slice(1, 3).map((box) => ({
      ...box,
      classType: "gap" as const,
      state: "unverified" as const,
      lifecycleState: "proposed" as const,
      reviewState: "unreviewed" as const,
      confidence: 0.8,
    }));
    const { container } = render(
      <AnnotationWorkspace
        fixture={{ ...DEMO_FIXTURE, annotations }}
        purpose="gap-review"
      />,
    );

    expect(
      container.querySelector(`.annotation[data-annotation-id="${annotations[0]!.id}"]`),
    ).toHaveClass("is-selected");
    fireEvent.click(screen.getByRole("button", { name: /Accept/ }));
    expect(
      container.querySelector(`.annotation[data-annotation-id="${annotations[1]!.id}"]`),
    ).toHaveClass("is-selected");
  });

  it("opens a gap review on its first unresolved box", () => {
    const annotations = [
      {
        ...DEMO_FIXTURE.annotations[1]!,
        classType: "gap" as const,
      },
      {
        ...DEMO_FIXTURE.annotations[2]!,
        classType: "gap" as const,
        state: "unverified" as const,
        lifecycleState: "proposed" as const,
        reviewState: "unreviewed" as const,
      },
    ];
    const { container } = render(
      <AnnotationWorkspace
        fixture={{ ...DEMO_FIXTURE, annotations }}
        purpose="gap-review"
      />,
    );

    expect(
      container.querySelector(`.annotation[data-annotation-id="${annotations[1]!.id}"]`),
    ).toHaveClass("is-selected");
  });

  it("shows timing lines after running the production benchmark", async () => {
    vi.stubGlobal(
      "requestAnimationFrame",
      (callback: FrameRequestCallback) => {
        setTimeout(() => callback(performance.now()), 0);
        return 1;
      },
    );
    render(
      <AnnotationWorkspace
        fixture={{
          ...DEMO_FIXTURE,
          name: "Benchmark test fixture",
          annotations: DEMO_FIXTURE.annotations.slice(0, 12),
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Measure" }));

    expect(
      await screen.findByTestId("benchmark-results", {}, { timeout: 5_000 }),
    ).toHaveTextContent("Browser frame baseline");
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent(
      "Canvas frame interval",
    );
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent(
      "Selection update",
    );
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent("Input latency");
    expect(screen.getByTestId("benchmark-timestamp")).not.toBeEmptyDOMElement();
  });

  it("operates accept, flag, duplicate, geometry, and reject from the keyboard", async () => {
    vi.useFakeTimers();
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Keyboard editing fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 4),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const selected = () =>
      container.querySelector<SVGGElement>(".annotation.is-selected");

    fireEvent.keyDown(viewport, { key: "a" });
    expect(selected()).toHaveAttribute("data-state", "verified");
    expect(screen.getByRole("button", { name: /Accepted/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Reject/ })).toBeEnabled();

    fireEvent.keyDown(viewport, { key: "f" });
    expect(selected()).toHaveAttribute("data-state", "flagged");

    const originalX = Number(selected()?.querySelector(".overlay-core")?.getAttribute("x"));
    fireEvent.keyDown(viewport, { key: "ArrowRight", altKey: true });
    expect(
      Number(selected()?.querySelector(".overlay-core")?.getAttribute("x")),
    ).toBe(originalX + 1);

    const originalWidth = Number(
      selected()?.querySelector(".overlay-core")?.getAttribute("width"),
    );
    fireEvent.keyDown(viewport, {
      key: "ArrowRight",
      altKey: true,
      shiftKey: true,
    });
    expect(
      Number(selected()?.querySelector(".overlay-core")?.getAttribute("width")),
    ).toBe(originalWidth + 1);

    fireEvent.keyDown(viewport, { key: "d" });
    expect(container.querySelectorAll(".annotation")).toHaveLength(4);
    expect(selected()?.getAttribute("data-annotation-id")).toContain("-copy-1");

    fireEvent.keyDown(viewport, { key: "r" });
    expect(container.querySelectorAll(".annotation")).toHaveLength(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(screen.getByText("saved", { selector: ".save-state" })).toBeInTheDocument();
  });

  it("does not run editing shortcuts from an input", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const search = screen.getByPlaceholderText("SKU, class, state, or ID");
    const selected = container.querySelector<SVGGElement>(".annotation.is-selected");

    fireEvent.keyDown(search, { key: "a" });
    fireEvent.keyDown(search, { key: "r" });

    expect(selected).toHaveAttribute("data-state", "flagged");
    expect(container.querySelectorAll(".annotation")).toHaveLength(320);
  });

  it("allows manual shelf-row correction for the selected facing", () => {
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Shelf row correction fixture",
      annotations: DEMO_FIXTURE.annotations.slice(0, 2),
    };
    render(<AnnotationWorkspace fixture={fixture} />);

    const rowInput = screen.getByLabelText("Shelf row number");
    expect(rowInput).toHaveValue(0);

    fireEvent.change(rowInput, { target: { value: "3" } });

    expect(rowInput).toHaveValue(3);
  });

  it("persists a manual shelf-row correction through autosave", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response(200, annotationResponse(2, "unreviewed", 3)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AnnotationWorkspace fixture={persistentFixture()} />);

    fireEvent.change(screen.getByLabelText("Shelf row number"), {
      target: { value: "3" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))).toMatchObject({
      expected_revision: 1,
      shelf_row: 3,
    });
  });

  it("converts a duplicated box into an unreviewed visible gap", () => {
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Gap annotation fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 2),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    fireEvent.keyDown(viewport, { key: "d" });
    fireEvent.change(screen.getByLabelText("Annotation class"), {
      target: { value: "gap" },
    });

    const selected = container.querySelector(".annotation.is-selected");
    expect(selected).toHaveAttribute("data-state", "unverified");
    expect(screen.getByLabelText("Annotation class")).toHaveValue("gap");
    expect(container.querySelector(".box-row.is-selected strong")).toHaveTextContent(
      "Visible gap",
    );
  });

  it("persists gap class without a SKU assignment", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response(200, gapAnnotationResponse(2)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AnnotationWorkspace fixture={persistentFixture()} />);

    fireEvent.change(screen.getByLabelText("Annotation class"), {
      target: { value: "gap" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))).toMatchObject({
      class_type: "gap",
      sku_id: null,
      review_state: "unreviewed",
    });
  });

  it("assigns candidates, repeats the previous SKU, and keeps Unknown after remount", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "SKU assignment fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 4),
    };
    const first = render(
      <AnnotationWorkspace fixture={fixture} mode="assign" />,
    );

    const search = await screen.findByLabelText("SKU assignment search");
    fireEvent.change(search, { target: { value: "aur cola 250 1" } });
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    fireEvent.keyDown(viewport, { key: "1" });
    expect(
      first.container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveTextContent("Aurora Cola 250ml 1 pack");

    fireEvent.keyDown(viewport, { key: "s" });
    expect(
      first.container.querySelector('.box-row[data-annotation-id="B003"]'),
    ).toHaveTextContent("Aurora Cola 250ml 1 pack");

    fireEvent.keyDown(viewport, { key: "u" });
    expect(
      first.container.querySelector('.box-row[data-annotation-id="B004"]'),
    ).toHaveTextContent("Unknown / Other");

    first.unmount();
    const second = render(
      <AnnotationWorkspace fixture={fixture} mode="assign" />,
    );
    expect(
      second.container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveTextContent("Aurora Cola 250ml 1 pack");
    expect(
      second.container.querySelector('.box-row[data-annotation-id="B004"]'),
    ).toHaveTextContent("Unknown / Other");
    expect(screen.getByText("Recovered local work")).toBeInTheDocument();
  });

  it("opens a floating SKU picker on box selection and accepts before assigning", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const proposed = {
      ...DEMO_FIXTURE.annotations[1]!,
      state: "unverified" as const,
      lifecycleState: "proposed" as const,
      reviewState: "unreviewed" as const,
      skuId: null,
      sku: "Unknown SKU",
    };
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Floating SKU picker fixture",
      annotations: [proposed],
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);

    fireEvent.pointerDown(container.querySelector(".annotation")!, { button: 0 });

    expect(screen.getByLabelText("SKU assignment")).toBeInTheDocument();
    expect(screen.getByLabelText("SKU assignment search")).toHaveFocus();
    fireEvent.click(
      screen.getAllByRole("button", { name: /Aurora Cola 250ml 1 pack/ })[0]!,
    );

    expect(container.querySelector(".annotation")).toHaveAttribute(
      "data-state",
      "verified",
    );
    expect(
      container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveTextContent("Aurora Cola 250ml 1 pack");
  });

  it("focuses assignment search with slash and ignores shortcuts inside it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "SKU assignment focus fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 2),
    };
    const { container } = render(
      <AnnotationWorkspace fixture={fixture} mode="assign" />,
    );
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const search = await screen.findByLabelText("SKU assignment search");

    fireEvent.keyDown(viewport, { key: "/" });
    expect(search).toHaveFocus();
    fireEvent.keyDown(search, { key: "u" });

    expect(
      container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).not.toHaveTextContent("Unknown / Other");
  });

  it("persists a server-backed assignment with its returned revision", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/skus")) {
        return Promise.resolve(response(200, {
          skus: [
            catalogSku(
              "00000000-0000-0000-0000-000000000001",
              "Unknown / Other",
              true,
            ),
            catalogSku(
              "00000000-0000-0000-0000-000000000003",
              "Live Catalog Cola",
              false,
            ),
          ],
        }));
      }
      return Promise.resolve(response(200, {
        id: "00000000-0000-0000-0000-000000000010",
        image_id: "00000000-0000-0000-0000-000000000020",
        revision: 2,
        x: DEMO_FIXTURE.annotations[1]!.x,
        y: DEMO_FIXTURE.annotations[1]!.y,
        width: DEMO_FIXTURE.annotations[1]!.width,
        height: DEMO_FIXTURE.annotations[1]!.height,
        class_type: "product",
        sku_id: "00000000-0000-0000-0000-000000000003",
        lifecycle_state: "verified",
        review_state: "accepted",
        source: "human",
        confidence: 0.75,
        occluded: false,
        truncated: false,
        shelf_row: 0,
      }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Persistent SKU assignment fixture",
      annotations: [
        {
          ...DEMO_FIXTURE.annotations[1]!,
          serverId: "00000000-0000-0000-0000-000000000010",
          imageId: "00000000-0000-0000-0000-000000000020",
          revision: 1,
        },
      ],
    };
    const first = render(
      <AnnotationWorkspace fixture={fixture} mode="assign" />,
    );
    fireEvent.change(screen.getByLabelText("Autosave"), {
      target: { value: "quick" },
    });

    fireEvent.click(await screen.findByRole("button", { name: /Live Catalog Cola/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/annotations/00000000-0000-0000-0000-000000000010/assign-sku",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(
      first.container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveTextContent("Live Catalog Cola");

    first.unmount();
    const second = render(
      <AnnotationWorkspace fixture={fixture} mode="assign" />,
    );
    expect(
      second.container.querySelector('.box-row[data-annotation-id="B002"]'),
    ).toHaveTextContent("Live Catalog Cola");
    expect(screen.getByText("Recovered local work")).toBeInTheDocument();
  });

  it("restores a locally acknowledged edit after remounting", () => {
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Draft recovery fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 2),
    };
    const first = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const originalX = fixture.annotations[0]!.x;

    fireEvent.keyDown(viewport, { key: "ArrowRight", altKey: true });
    first.unmount();
    const second = render(<AnnotationWorkspace fixture={fixture} />);

    expect(
      Number(
        second.container
          .querySelector(".annotation.is-selected .overlay-core")
          ?.getAttribute("x"),
      ),
    ).toBe(originalX + 1);
    expect(screen.getByText("Recovered local work")).toBeInTheDocument();
  });

  it("keeps failed network edits locally and shows offline status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<AnnotationWorkspace fixture={persistentFixture()} />);
    fireEvent.change(screen.getByLabelText("Autosave"), {
      target: { value: "quick" },
    });

    fireEvent.keyDown(screen.getByLabelText("Shelf annotation canvas"), {
      key: "f",
    });

    expect(
      await screen.findByText("offline", { selector: ".save-state" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Offline. Local changes are safe")).toBeInTheDocument();
    expect(localStorage.length).toBe(1);
  });

  it("supports manual save without sending edits early", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(response(200, annotationResponse(2, "flagged")));
    vi.stubGlobal("fetch", fetchMock);
    render(<AnnotationWorkspace fixture={persistentFixture()} />);
    fireEvent.change(screen.getByLabelText("Autosave"), {
      target: { value: "manual" },
    });

    fireEvent.keyDown(screen.getByLabelText("Shelf annotation canvas"), {
      key: "f",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save now" }));
    expect(
      await screen.findByText("saved", { selector: ".save-state" }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("preserves local and server versions when optimistic concurrency conflicts", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(409, {
        detail: { code: "stale_revision", message: "stale" },
      }))
      .mockResolvedValueOnce(response(200, annotationResponse(2, "unreviewed")))
      .mockResolvedValueOnce(response(200, annotationResponse(3, "flagged")));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <AnnotationWorkspace fixture={persistentFixture()} />,
    );
    fireEvent.change(screen.getByLabelText("Autosave"), {
      target: { value: "quick" },
    });

    fireEvent.keyDown(screen.getByLabelText("Shelf annotation canvas"), {
      key: "f",
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Both versions are preserved",
    );
    expect(
      container.querySelector(".annotation.is-selected"),
    ).toHaveAttribute("data-state", "flagged");

    fireEvent.click(screen.getByRole("button", { name: "Keep my edit" }));
    await waitFor(() =>
      expect(
        screen.getByText("saved", { selector: ".save-state" }),
      ).toBeInTheDocument(),
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

function persistentFixture() {
  return {
    ...DEMO_FIXTURE,
    name: "Persistent editing fixture",
    annotations: [
      {
        ...DEMO_FIXTURE.annotations[1]!,
        id: "local-1",
        serverId: "00000000-0000-0000-0000-000000000001",
        imageId: "00000000-0000-0000-0000-000000000002",
        revision: 1,
        state: "unverified" as const,
        lifecycleState: "proposed" as const,
        reviewState: "unreviewed" as const,
      },
    ],
  };
}

function annotationResponse(
  revision: number,
  reviewState: "unreviewed" | "flagged",
  shelfRow = 0,
) {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    image_id: "00000000-0000-0000-0000-000000000002",
    revision,
    x: 10,
    y: 10,
    width: 20,
    height: 30,
    class_type: "product",
    sku_id: null,
    lifecycle_state: "proposed",
    review_state: reviewState,
    source: "human",
    confidence: 0.8,
    occluded: false,
    truncated: false,
    shelf_row: shelfRow,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}

function gapAnnotationResponse(revision: number) {
  return {
    ...annotationResponse(revision, "unreviewed"),
    class_type: "gap",
    sku_id: null,
  };
}

function catalogSku(id: string, name: string, isUnknown: boolean) {
  return {
    id,
    name,
    upc: null,
    category: null,
    subcategory: null,
    brand: null,
    variant: null,
    is_unknown: isUnknown,
    status: "active",
    merged_into_id: null,
    reference_images: [],
  };
}
