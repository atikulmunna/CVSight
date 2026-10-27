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
    expect(container.querySelectorAll(".selection-handle")).toHaveLength(8);
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

  it("switches tools from the rail and keyboard and pans with the hand tool", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const scene = container.querySelector<HTMLElement>(".canvas-scene");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });
    const pressed = (name: string) =>
      screen.getByRole("button", { name }).getAttribute("aria-pressed");

    expect(pressed("Select")).toBe("true");
    fireEvent.keyDown(viewport, { key: "h" });
    expect(pressed("Pan")).toBe("true");
    expect(viewport).toHaveClass("is-pan-ready");
    const before = scene?.style.transform;
    drag(viewport, { x: 100, y: 100 }, { x: 160, y: 140 });
    expect(scene?.style.transform).not.toBe(before);
    expect(container.querySelector(".annotation-overlay .annotation.is-selected")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Draw box" }));
    expect(pressed("Draw box")).toBe("true");
    expect(viewport).toHaveClass("is-drawing-product");
    expect(viewport).not.toHaveClass("is-pan-ready");

    fireEvent.keyDown(viewport, { key: "v" });
    expect(pressed("Select")).toBe("true");
    fireEvent.keyDown(viewport, { key: "g" });
    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(pressed("Select")).toBe("true");
  });

  it("offers only selection and panning while assigning SKUs", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(<AnnotationWorkspace fixture={DEMO_FIXTURE} mode="assign" />);
    const tools = screen.getByRole("toolbar", { name: "Canvas tools" });

    expect(
      [...tools.querySelectorAll("button")].map((button) => button.getAttribute("aria-label")),
    ).toEqual(["Select", "Pan", "Undo", "Redo"]);
  });

  it("resizes the selected box from a handle and moves it by dragging", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Geometry drag fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 2),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });
    const core = () => container.querySelector<SVGRectElement>(".annotation.is-selected .overlay-core")!;
    const geometry = (): [number, number, number, number] => {
      const rect = core();
      const read = (key: string) => Number(rect.getAttribute(key));
      return [read("x"), read("y"), read("width"), read("height")];
    };
    const [x, y, width, height] = geometry();

    const corner = container.querySelector('.annotation.is-selected [data-handle="se"]')!;
    fireEvent.pointerDown(corner, { button: 0, pointerId: 1, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 340, clientY: 330 });
    fireEvent.pointerUp(viewport, { pointerId: 1, clientX: 340, clientY: 330 });
    const [x2, y2, width2, height2] = geometry();
    expect([x2, y2]).toEqual([x, y]);
    expect(width2).toBeGreaterThan(width);
    expect(height2).toBeGreaterThan(height);

    fireEvent.pointerDown(core(), { button: 0, pointerId: 2, clientX: 300, clientY: 300 });
    fireEvent.pointerMove(viewport, { pointerId: 2, clientX: 350, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 350, clientY: 300 });
    const [x3, y3, width3, height3] = geometry();
    expect(x3).toBeGreaterThan(x2);
    expect([y3, width3, height3]).toEqual([y2, width2, height2]);

    fireEvent.pointerDown(core(), { button: 0, pointerId: 3, clientX: 300, clientY: 300 });
    fireEvent.pointerUp(viewport, { pointerId: 3, clientX: 301, clientY: 300 });
    expect(geometry()).toEqual([x3, y3, width3, height3]);
    expect(screen.getByLabelText("SKU assignment")).toBeInTheDocument();
  });

  it("resizes a just-drawn box from its handle without leaving the drawing tool", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = { ...DEMO_FIXTURE, name: "Draw then resize fixture", annotations: [] };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });

    fireEvent.keyDown(viewport, { key: "b" });
    drag(viewport, { x: 200, y: 200 }, { x: 320, y: 330 });
    const core = () => container.querySelector<SVGRectElement>(".annotation.is-selected .overlay-core")!;
    const width = Number(core().getAttribute("width"));

    const edge = container.querySelector('.annotation.is-selected [data-handle="e"]')!;
    fireEvent.pointerDown(edge, { button: 0, pointerId: 4, clientX: 320, clientY: 260 });
    fireEvent.pointerUp(viewport, { pointerId: 4, clientX: 360, clientY: 260 });

    expect(Number(core().getAttribute("width"))).toBeGreaterThan(width);
    expect(container.querySelectorAll(".box-row")).toHaveLength(1);
    expect(viewport).toHaveClass("is-drawing-product");
  });

  it("draws a product, opens its SKU picker, and stays on it after naming it", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const existing = {
      ...DEMO_FIXTURE.annotations[1]!,
      state: "unverified" as const,
      lifecycleState: "proposed" as const,
      reviewState: "unreviewed" as const,
    };
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Draw product fixture",
      annotations: [existing],
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });

    fireEvent.keyDown(viewport, { key: "b" });
    expect(screen.getByRole("button", { name: "Draw box" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(viewport).toHaveClass("is-drawing-product");

    drag(viewport, { x: 200, y: 200 }, { x: 320, y: 330 });

    const drawn = container.querySelector<HTMLElement>('.box-row[data-annotation-id^="product-"]');
    expect(drawn).toHaveTextContent("Unassigned");
    expect(drawn).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("SKU assignment")).toBeInTheDocument();

    fireEvent.click(
      screen.getAllByRole("button", { name: /Aurora Cola 250ml 1 pack/ })[0]!,
    );

    const named = container.querySelector<HTMLElement>('.box-row[data-annotation-id^="product-"]');
    expect(named).toHaveTextContent("Aurora Cola 250ml 1 pack");
    expect(named).toHaveAttribute("aria-selected", "true");
    expect(viewport).toHaveClass("is-drawing-product");
    expect(screen.queryByLabelText("SKU assignment")).not.toBeInTheDocument();
    expect(viewport).toHaveFocus();

    drag(viewport, { x: 420, y: 200 }, { x: 520, y: 330 });
    const rows = () =>
      [...container.querySelectorAll<HTMLElement>('.box-row[data-annotation-id^="product-"]')];
    expect(rows()).toHaveLength(2);
    const repeated = rows().find((row) => row.getAttribute("aria-selected") === "true")!;
    expect(repeated).toHaveTextContent("Aurora Cola 250ml 1 pack");
    expect(
      container.querySelector(
        `.annotation[data-annotation-id="${repeated.getAttribute("data-annotation-id")}"]`,
      ),
    ).toHaveAttribute("data-state", "verified");
    expect(screen.queryByLabelText("SKU assignment")).not.toBeInTheDocument();

    fireEvent.keyDown(viewport, { key: "/" });
    expect(screen.getByLabelText("SKU assignment")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Delete box/ }));
    expect(rows()).toHaveLength(1);
    expect(screen.queryByLabelText("SKU assignment")).not.toBeInTheDocument();
    expect(container.querySelector(".box-row.is-selected")).toBeNull();

    drag(viewport, { x: 420, y: 200 }, { x: 520, y: 330 });
    expect(rows()).toHaveLength(2);
    fireEvent.keyDown(viewport, { key: "Delete" });
    expect(rows()).toHaveLength(1);
    expect(viewport).toHaveClass("is-drawing-product");

    fireEvent.keyDown(viewport, { key: "Escape" });
    expect(viewport).not.toHaveClass("is-drawing");
  });

  it("ignores a click that is too small to be a box", () => {
    const fixture = { ...DEMO_FIXTURE, name: "Tiny drag fixture", annotations: [] };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });

    fireEvent.click(screen.getByRole("button", { name: /Draw box/ }));
    drag(viewport, { x: 200, y: 200 }, { x: 200, y: 200 });

    expect(container.querySelectorAll(".box-row")).toHaveLength(0);
  });

  it("keeps product drawing out of gap review", () => {
    render(
      <AnnotationWorkspace
        fixture={{ ...DEMO_FIXTURE, name: "Gap only fixture" }}
        purpose="gap-review"
      />,
    );
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    expect(screen.queryByRole("button", { name: /Draw box/ })).not.toBeInTheDocument();
    fireEvent.keyDown(viewport, { key: "b" });
    expect(viewport).not.toHaveClass("is-drawing");
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
      await screen.findByTestId("benchmark-results", {}, { timeout: 15_000 }),
    ).toHaveTextContent("Browser frame baseline");
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent(
      "Canvas frame interval",
    );
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent(
      "Selection update",
    );
    expect(screen.getByTestId("benchmark-results")).toHaveTextContent("Input latency");
    expect(screen.getByTestId("benchmark-timestamp")).not.toBeEmptyDOMElement();
    // The benchmark runs roughly 240 animation frames and 245 synchronous renders,
    // so the test needs a budget larger than the wait above. The default 5 second
    // test timeout matched that wait exactly, which left the wait unusable and made
    // the test fail on slower machines.
  }, 30_000);

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

  it("moves to the next undecided box after every decision", () => {
    const proposed = DEMO_FIXTURE.annotations.slice(1, 4).map((box) => ({
      ...box,
      state: "unverified" as const,
      lifecycleState: "proposed" as const,
      reviewState: "unreviewed" as const,
    }));
    const fixture = { ...DEMO_FIXTURE, name: "Decision flow fixture", annotations: proposed };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const selectedId = () =>
      container.querySelector(".annotation.is-selected")?.getAttribute("data-annotation-id");
    const [first, second, third] = [...container.querySelectorAll(".box-row")].map((row) =>
      row.getAttribute("data-annotation-id"),
    );

    expect(selectedId()).toBe(first);
    expect(viewport).toHaveFocus();
    fireEvent.keyDown(viewport, { key: "a" });
    expect(selectedId()).toBe(second);

    fireEvent.keyDown(viewport, { key: "r" });
    expect(selectedId()).toBe(third);

    fireEvent.click(screen.getByRole("button", { name: /^Accept/ }));
    expect(selectedId()).toBeUndefined();
    expect(viewport).toHaveFocus();
    expect(screen.getByText("Choose a box on the canvas or in the list.")).toBeInTheDocument();
  });

  it("undoes and redoes decisions and geometry from the keyboard and the tool rail", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const proposed = DEMO_FIXTURE.annotations.slice(1, 3).map((box) => ({
      ...box,
      state: "unverified" as const,
      lifecycleState: "proposed" as const,
      reviewState: "unreviewed" as const,
    }));
    const fixture = { ...DEMO_FIXTURE, name: "Undo fixture", annotations: proposed };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const undo = screen.getByRole("button", { name: "Undo" });
    const redo = screen.getByRole("button", { name: "Redo" });
    const selected = () => container.querySelector<SVGGElement>(".annotation.is-selected");
    const firstId = selected()!.getAttribute("data-annotation-id")!;
    const first = () => container.querySelector(`.annotation[data-annotation-id="${firstId}"]`);
    const selectedX = () =>
      Number(selected()?.querySelector(".overlay-core")?.getAttribute("x"));

    expect(undo).toBeDisabled();
    expect(redo).toBeDisabled();
    fireEvent.keyDown(viewport, { key: "a" });
    expect(first()).toHaveAttribute("data-state", "verified");
    expect(first()).not.toHaveClass("is-selected");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(first()).toHaveAttribute("data-state", "unverified");
    expect(first()).toHaveClass("is-selected");
    expect(redo).toBeEnabled();

    fireEvent.keyDown(viewport, { key: "Z", ctrlKey: true, shiftKey: true });
    expect(first()).toHaveAttribute("data-state", "verified");
    expect(redo).toBeDisabled();

    fireEvent.click(undo);
    expect(first()).toHaveAttribute("data-state", "unverified");
    fireEvent.click(redo);
    expect(first()).toHaveAttribute("data-state", "verified");

    fireEvent.keyDown(viewport, { key: "z", metaKey: true });
    const originalX = selectedX();
    fireEvent.keyDown(viewport, { key: "ArrowRight", altKey: true });
    expect(selectedX()).toBe(originalX + 1);
    fireEvent.keyDown(viewport, { key: "y", ctrlKey: true });
    expect(selectedX()).toBe(originalX + 1);
    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(selectedX()).toBe(originalX);
    expect(first()).toHaveAttribute("data-state", "unverified");
  });

  it("undoes drawing and naming a box as one step and redoes it", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = { ...DEMO_FIXTURE, name: "Undo drawing fixture", annotations: [] };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    Object.defineProperty(viewport, "setPointerCapture", { value: () => undefined });
    const rows = () => [...container.querySelectorAll<HTMLElement>(".box-row")];

    fireEvent.keyDown(viewport, { key: "b" });
    drag(viewport, { x: 200, y: 200 }, { x: 320, y: 330 });
    fireEvent.click(
      screen.getAllByRole("button", { name: /Aurora Cola 250ml 1 pack/ })[0]!,
    );
    drag(viewport, { x: 420, y: 200 }, { x: 520, y: 330 });
    expect(rows()).toHaveLength(2);
    expect(rows()[1]).toHaveTextContent("Aurora Cola 250ml 1 pack");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent("Aurora Cola 250ml 1 pack");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(rows()[0]).toHaveTextContent("Unassigned");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(rows()).toHaveLength(0);

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true, shiftKey: true });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent("Unassigned");
    expect(rows()[0]).toHaveAttribute("aria-selected", "true");
  });

  it("undoes a saved acceptance by reopening the box at its new revision", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(
        String(input).endsWith("/accept")
          ? response(200, {
              ...annotationResponse(2, "unreviewed"),
              lifecycle_state: "verified",
              review_state: "accepted",
            })
          : response(200, annotationResponse(3, "unreviewed")),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const base = persistentFixture();
    // The box matches what the server echoes, as it would outside a test.
    const fixture = {
      ...base,
      annotations: [
        {
          ...base.annotations[0]!,
          x: 10,
          y: 10,
          width: 20,
          height: 30,
          skuId: null,
          confidence: 0.8,
          occluded: false,
          truncated: false,
          shelfRow: 0,
        },
      ],
    };
    render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    fireEvent.keyDown(viewport, { key: "a" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/annotations/00000000-0000-0000-0000-000000000001/accept",
      "/api/annotations/00000000-0000-0000-0000-000000000001/reopen",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))).toEqual({
      expected_revision: 2,
    });
    expect(screen.getByText("saved", { selector: ".save-state" })).toBeInTheDocument();
  });

  it("leaves Ctrl and Cmd combinations to the browser instead of editing", () => {
    const { container } = render(<AnnotationWorkspace fixture={DEMO_FIXTURE} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const selected = container.querySelector<SVGGElement>(".annotation.is-selected");
    const state = selected?.getAttribute("data-state");

    fireEvent.keyDown(viewport, { key: "r", ctrlKey: true });
    fireEvent.keyDown(viewport, { key: "f", metaKey: true });
    fireEvent.keyDown(viewport, { key: "d", ctrlKey: true });

    expect(container.querySelectorAll(".annotation")).toHaveLength(320);
    expect(selected).toHaveAttribute("data-state", state!);
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("selects several boxes with Shift and decides them in one undoable step", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Group decision fixture",
      annotations: proposedBoxes(3),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const [first, second, third] = rowIds(container);
    const overlay = (id: string) =>
      container.querySelector(`.annotation[data-annotation-id="${id}"]`)!;

    fireEvent.pointerDown(overlay(second!), { button: 0, shiftKey: true });
    expect(overlay(first!)).toHaveClass("is-selected");
    expect(overlay(second!)).toHaveClass("is-selected");
    expect(container.querySelectorAll(".selection-handle")).toHaveLength(0);
    expect(screen.getByText("2 boxes", { selector: ".detail-panel strong" })).toBeInTheDocument();

    fireEvent.keyDown(viewport, { key: "a" });
    expect(overlay(first!)).toHaveAttribute("data-state", "verified");
    expect(overlay(second!)).toHaveAttribute("data-state", "verified");
    expect(overlay(third!)).toHaveAttribute("data-state", "unverified");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(overlay(first!)).toHaveAttribute("data-state", "unverified");
    expect(overlay(second!)).toHaveAttribute("data-state", "unverified");

    fireEvent.click(container.querySelector(`.box-row[data-annotation-id="${first}"]`)!, {
      shiftKey: true,
    });
    expect(overlay(first!)).not.toHaveClass("is-selected");
    expect(overlay(second!)).toHaveClass("is-selected");
    expect(container.querySelectorAll(".selection-handle")).toHaveLength(8);
  });

  it("changes the class of a group and rejects it, then brings it back", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Group reject fixture",
      annotations: proposedBoxes(3),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const [first, second] = rowIds(container);
    const row = (id: string) =>
      container.querySelector(`.box-row[data-annotation-id="${id}"]`)!;

    fireEvent.click(row(second!), { shiftKey: true });
    fireEvent.change(screen.getByLabelText("Annotation class"), {
      target: { value: "gap" },
    });
    expect(row(first!)).toHaveTextContent("Visible gap");
    expect(row(second!)).toHaveTextContent("Visible gap");

    fireEvent.click(screen.getByRole("button", { name: /Reject all/ }));
    expect(container.querySelectorAll(".box-row")).toHaveLength(1);
    expect(screen.getByText("Choose a box on the canvas or in the list.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(container.querySelectorAll(".box-row")).toHaveLength(3);
    expect(screen.getByText("2 boxes", { selector: ".detail-panel strong" })).toBeInTheDocument();
  });

  it("selects the boxes inside a dragged marquee and clears on a click beside the photo", () => {
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Marquee fixture",
      annotations: proposedBoxes(3),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const selectedCount = () => container.querySelectorAll(".annotation.is-selected").length;

    drag(viewport, { x: 0, y: 0 }, { x: 5_000, y: 5_000 });
    expect(selectedCount()).toBe(3);
    expect(container.querySelector(".selection-marquee")).toBeNull();

    fireEvent.pointerDown(viewport, { button: 0, pointerId: 2, clientX: 5, clientY: 5 });
    fireEvent.pointerUp(viewport, { pointerId: 2, clientX: 5, clientY: 5 });
    expect(selectedCount()).toBe(0);

    fireEvent.keyDown(viewport, { key: "ArrowRight" });
    expect(selectedCount()).toBe(1);
    fireEvent.pointerDown(viewport, {
      button: 0,
      pointerId: 3,
      shiftKey: true,
      clientX: 6_000,
      clientY: 6_000,
    });
    fireEvent.pointerMove(viewport, { pointerId: 3, clientX: 6_100, clientY: 6_100 });
    expect(container.querySelector(".selection-marquee")).not.toBeNull();
    fireEvent.pointerUp(viewport, { pointerId: 3, clientX: 6_100, clientY: 6_100 });
    expect(selectedCount()).toBe(1);

    drag(viewport, { x: 6_000, y: 6_000 }, { x: 6_100, y: 6_100 });
    expect(selectedCount()).toBe(0);
  });

  it("selects every box with Ctrl+A", () => {
    const fixture = { ...DEMO_FIXTURE, name: "Select all fixture", annotations: proposedBoxes(3) };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);

    fireEvent.keyDown(screen.getByLabelText("Shelf annotation canvas"), {
      key: "a",
      ctrlKey: true,
    });

    expect(container.querySelectorAll(".annotation.is-selected")).toHaveLength(3);
    expect(container.querySelectorAll('.box-row[aria-selected="true"]')).toHaveLength(3);
    expect(container.querySelectorAll(".annotation")[0]).toHaveAttribute(
      "data-state",
      "unverified",
    );
  });

  it("copies boxes and pastes them offset on the same photo and in place on another", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    const source = { ...DEMO_FIXTURE, name: "Copy source", annotations: proposedBoxes(1) };
    const original = source.annotations[0]!;
    const first = render(<AnnotationWorkspace fixture={source} />);
    let viewport = screen.getByLabelText("Shelf annotation canvas");
    const selectedX = (container: HTMLElement) =>
      Number(container.querySelector(".annotation.is-selected .overlay-core")?.getAttribute("x"));

    fireEvent.keyDown(viewport, { key: "c", ctrlKey: true });
    fireEvent.keyDown(viewport, { key: "v", ctrlKey: true });
    expect(first.container.querySelectorAll(".annotation")).toHaveLength(2);
    expect(
      first.container.querySelector(".annotation.is-selected")?.getAttribute("data-annotation-id"),
    ).toMatch(/^pasted-/);
    expect(selectedX(first.container)).toBe(original.x + 8);

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(first.container.querySelectorAll(".annotation")).toHaveLength(1);
    first.unmount();

    const target = {
      ...DEMO_FIXTURE,
      name: "Paste target",
      images: [{ ...DEMO_FIXTURE.images[0]!, id: "other-shelf" }],
      annotations: [],
    };
    const second = render(<AnnotationWorkspace fixture={target} />);
    viewport = screen.getByLabelText("Shelf annotation canvas");
    fireEvent.keyDown(viewport, { key: "v", metaKey: true });

    expect(second.container.querySelectorAll(".annotation")).toHaveLength(1);
    expect(selectedX(second.container)).toBe(original.x);
    expect(second.container.querySelector(".annotation")).toHaveAttribute(
      "data-state",
      "unverified",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/images/other-shelf/annotations",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("names every selected product with one SKU while assigning", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Group assignment fixture",
      annotations: DEMO_FIXTURE.annotations.slice(1, 4),
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} mode="assign" />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const row = (id: string) =>
      container.querySelector(`.box-row[data-annotation-id="${id}"]`)!;
    await screen.findByLabelText("SKU assignment search");

    fireEvent.click(row("B003"), { shiftKey: true });
    expect(screen.getByText("Assign identity to 2 boxes")).toBeInTheDocument();
    fireEvent.keyDown(viewport, { key: "u" });

    expect(row("B002")).toHaveTextContent("Unknown / Other");
    expect(row("B003")).toHaveTextContent("Unknown / Other");
    expect(row("B004")).not.toHaveTextContent("Unknown / Other");
  });

  it("lists the shortcuts for the current mode with ? and returns to the canvas", () => {
    const fixture = { ...DEMO_FIXTURE, name: "Help fixture", annotations: proposedBoxes(2) };
    const first = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    fireEvent.keyDown(viewport, { key: "?", shiftKey: true });
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(dialog).toHaveTextContent("Draw a box, named with the previous SKU");
    expect(dialog).not.toHaveTextContent("Assign a candidate SKU");
    expect(screen.getByRole("button", { name: "Close shortcuts" })).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(viewport).toHaveFocus();
    first.unmount();

    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    render(
      <AnnotationWorkspace
        fixture={{ ...DEMO_FIXTURE, annotations: DEMO_FIXTURE.annotations.slice(1, 3) }}
        mode="assign"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /All shortcuts/ }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Assign a candidate SKU");
    expect(screen.getByRole("dialog")).not.toHaveTextContent("Draw a gap");
  });

  it("hides boxes by class or all at once and keeps class choices for the next photo", () => {
    const fixture = { ...DEMO_FIXTURE, name: "Hide fixture", annotations: proposedBoxes(2) };
    const first = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const scene = () => first.container.querySelector(".canvas-scene")!;

    expect(screen.queryByText("Some boxes hidden")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Gaps" }));
    expect(scene()).toHaveClass("hides-gap");
    expect(screen.getByText("Some boxes hidden")).toBeInTheDocument();

    fireEvent.keyDown(viewport, { key: "o" });
    expect(scene()).toHaveClass("hides-all");
    expect(screen.getByRole("checkbox", { name: "Products" })).toBeDisabled();
    fireEvent.keyDown(viewport, { key: "o" });
    expect(scene()).not.toHaveClass("hides-all");

    fireEvent.keyDown(viewport, { key: "o" });
    first.unmount();
    const second = render(<AnnotationWorkspace fixture={fixture} />);
    const sceneAfter = second.container.querySelector(".canvas-scene")!;
    expect(sceneAfter).toHaveClass("hides-gap");
    expect(sceneAfter).not.toHaveClass("hides-all");
  });

  it("adjusts photo brightness, contrast, and box fill without touching annotations", () => {
    const fixture = { ...DEMO_FIXTURE, name: "Display fixture", annotations: proposedBoxes(2) };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const scene = container.querySelector<HTMLElement>(".canvas-scene")!;
    const imageLayer = container.querySelector<HTMLElement>(".image-layer")!;

    fireEvent.change(screen.getByLabelText(/Brightness/), { target: { value: "150" } });
    fireEvent.change(screen.getByLabelText(/Contrast/), { target: { value: "80" } });
    fireEvent.change(screen.getByLabelText(/Box fill/), { target: { value: "30" } });

    expect(imageLayer.style.filter).toBe("brightness(1.5) contrast(0.8)");
    expect(scene.style.getPropertyValue("--ov-fill-op")).toBe("0.3");
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(imageLayer.style.filter).toBe("");
    expect(scene.style.getPropertyValue("--ov-fill-op")).toBe("0");
  });

  it("shows a crosshair at the pointer only while drawing", () => {
    const { container } = render(
      <AnnotationWorkspace fixture={{ ...DEMO_FIXTURE, annotations: proposedBoxes(1) }} />,
    );
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const crosshair = () => container.querySelector<HTMLElement>(".draw-crosshair");

    expect(crosshair()).toBeNull();
    fireEvent.keyDown(viewport, { key: "b" });
    fireEvent.pointerMove(viewport, { pointerId: 1, clientX: 120, clientY: 80 });
    expect(crosshair()).toHaveClass("is-visible");
    expect(crosshair()!.style.getPropertyValue("--crosshair-x")).toBe("120px");
    expect(crosshair()!.style.getPropertyValue("--crosshair-y")).toBe("80px");

    fireEvent.pointerLeave(viewport);
    expect(crosshair()).not.toHaveClass("is-visible");
    fireEvent.keyDown(viewport, { key: "v" });
    expect(crosshair()).toBeNull();
  });

  it("boxes the product under a smart click and undoes it in one step", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      String(input).endsWith("/smart-box")
        ? Promise.resolve(response(200, { x: 100, y: 120, width: 60, height: 90, score: 0.9 }))
        : Promise.reject(new TypeError("offline")),
    );
    vi.stubGlobal("fetch", fetchMock);
    const fixture = { ...DEMO_FIXTURE, name: "Smart box fixture", annotations: [] };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    fireEvent.keyDown(viewport, { key: "m" });
    expect(screen.getByRole("button", { name: "Smart box" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.pointerDown(viewport, { button: 0, pointerId: 1, clientX: 250, clientY: 250 });

    await waitFor(() => expect(container.querySelectorAll(".box-row")).toHaveLength(1));
    const core = container.querySelector(".annotation.is-selected .overlay-core")!;
    expect([core.getAttribute("x"), core.getAttribute("width")]).toEqual(["100", "60"]);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/images/${DEMO_FIXTURE.images[0]!.id}/smart-box`,
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(container.querySelectorAll(".box-row")).toHaveLength(0);
  });

  it("explains a failed smart box and ignores clicks while one is pending", async () => {
    let answer: (value: unknown) => void = () => undefined;
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <AnnotationWorkspace fixture={{ ...DEMO_FIXTURE, annotations: [] }} />,
    );
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const click = { button: 0, pointerId: 1, clientX: 250, clientY: 250 };

    fireEvent.keyDown(viewport, { key: "m" });
    fireEvent.pointerDown(viewport, click);
    fireEvent.pointerDown(viewport, click);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".smart-box-pending")).not.toBeNull();

    await act(async () => {
      answer(response(503, { detail: { code: "smart_box_unavailable" } }));
    });

    expect(
      await screen.findByText("Smart box is off: no SAM service is configured."),
    ).toHaveAttribute("role", "status");
    expect(container.querySelector(".smart-box-pending")).toBeNull();
    expect(container.querySelectorAll(".box-row")).toHaveLength(0);
  });

  it("outlines a product, refines it, and saves it with its box in one undo step", async () => {
    // A rectangle traced with a point halfway along each side: four corners carry its
    // shape, so the outline opens at four points of eight.
    const traced = [
      [100, 120], [130, 120], [160, 120], [160, 165],
      [160, 210], [130, 210], [100, 210], [100, 165],
    ];
    const trimmed = [[100, 120], [150, 120], [150, 210], [100, 210]];
    const outlineRequests: unknown[] = [];
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (!String(input).endsWith("/smart-polygon")) {
        return Promise.reject(new TypeError("offline"));
      }
      const body = JSON.parse(String(init?.body)) as { points: unknown[] };
      outlineRequests.push(body);
      const polygon = body.points.length === 1 ? traced : trimmed;
      return Promise.resolve(response(200, { polygon, score: 0.9 }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const fixture = {
      ...DEMO_FIXTURE,
      projectType: "instance_segmentation" as const,
      annotations: [],
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");
    const draftOutline = () =>
      container.querySelector('[data-annotation-id="smart-polygon-draft"] .overlay-core');

    fireEvent.keyDown(viewport, { key: "m" });
    expect(screen.getByRole("button", { name: "Smart polygon" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.pointerDown(viewport, { button: 0, pointerId: 1, clientX: 250, clientY: 250 });

    const slider = await screen.findByRole("slider", { name: "Points" });
    expect(slider).toHaveValue("4");
    expect(slider).toHaveAttribute("max", "8");
    expect(draftOutline()?.getAttribute("points")).toBe("100,120 160,120 160,210 100,210");
    expect(container.querySelectorAll(".box-row")).toHaveLength(0);

    fireEvent.change(slider, { target: { value: "8" } });
    expect(draftOutline()?.getAttribute("points")?.split(" ")).toHaveLength(8);

    fireEvent.pointerDown(viewport, {
      button: 0,
      pointerId: 1,
      clientX: 300,
      clientY: 250,
      shiftKey: true,
    });
    await waitFor(() =>
      expect(draftOutline()?.getAttribute("points")).toBe("100,120 150,120 150,210 100,210"),
    );
    expect(outlineRequests[1]).toMatchObject({
      points: [{ include: true }, { include: false }],
    });
    expect(container.querySelectorAll(".smart-click.is-excluded")).toHaveLength(1);

    fireEvent.keyDown(viewport, { key: "Enter" });

    expect(container.querySelectorAll(".box-row")).toHaveLength(1);
    expect(screen.queryByRole("slider", { name: "Points" })).not.toBeInTheDocument();
    const saved = container.querySelector(".annotation.is-selected .overlay-core")!;
    expect(saved.tagName).toBe("polygon");
    expect(saved.getAttribute("points")).toBe("100,120 150,120 150,210 100,210");

    fireEvent.keyDown(viewport, { key: "z", ctrlKey: true });
    expect(container.querySelectorAll(".box-row")).toHaveLength(0);
  });

  it("cancels a smart polygon outline with Escape and keeps the tool", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(200, { polygon: [[100, 120], [160, 120], [130, 210]], score: 0.9 }),
      ),
    );
    const fixture = {
      ...DEMO_FIXTURE,
      projectType: "instance_segmentation" as const,
      annotations: [],
    };
    const { container } = render(<AnnotationWorkspace fixture={fixture} />);
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    fireEvent.keyDown(viewport, { key: "m" });
    fireEvent.pointerDown(viewport, {
      button: 0,
      pointerId: 1,
      clientX: 250,
      clientY: 250,
      shiftKey: true,
    });
    expect(
      screen.getByText("Click the product first; Shift+click then removes areas from its outline."),
    ).toBeInTheDocument();

    fireEvent.pointerDown(viewport, { button: 0, pointerId: 1, clientX: 250, clientY: 250 });
    await screen.findByRole("slider", { name: "Points" });
    fireEvent.keyDown(viewport, { key: "Escape" });

    expect(screen.queryByRole("slider", { name: "Points" })).not.toBeInTheDocument();
    expect(container.querySelectorAll(".box-row")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Smart polygon" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps the smart box out of gap review", () => {
    render(<AnnotationWorkspace fixture={DEMO_FIXTURE} purpose="gap-review" />);

    expect(screen.queryByRole("button", { name: "Smart box" })).not.toBeInTheDocument();
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

  it("saves an edit made while the previous save is still in flight", async () => {
    vi.useFakeTimers();
    let finishFirstSave: (value: unknown) => void = () => undefined;
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirstSave = resolve;
          }),
      )
      .mockResolvedValue(response(200, annotationResponse(3, "unreviewed", 3)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AnnotationWorkspace fixture={persistentFixture()} />);
    const rowInput = screen.getByLabelText("Shelf row number");

    fireEvent.change(rowInput, { target: { value: "2" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.change(rowInput, { target: { value: "3" } });
    await act(async () => {
      finishFirstSave(response(200, annotationResponse(2, "unreviewed", 2)));
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))).toMatchObject({
      expected_revision: 2,
      shelf_row: 3,
    });
    expect(rowInput).toHaveValue(3);
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
    const viewport = screen.getByLabelText("Shelf annotation canvas");

    // The box opens selected, so a click completes on release (a press could start a move).
    const click = { button: 0, pointerId: 1, clientX: 100, clientY: 100 };
    fireEvent.pointerDown(container.querySelector(".annotation")!, click);
    fireEvent.pointerUp(viewport, click);

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
    expect(screen.queryByLabelText("SKU assignment")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Shelf annotation canvas")).toHaveFocus();
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
        polygon: null,
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

  it("prefers a fresh server copy over a saved draft of the same revision", () => {
    const saved = {
      ...DEMO_FIXTURE.annotations[1]!,
      id: "00000000-0000-4000-8000-000000000011",
      serverId: "00000000-0000-4000-8000-000000000011",
      revision: 2,
      skuId: "00000000-0000-4000-8000-000000000031",
    };
    const added = {
      ...DEMO_FIXTURE.annotations[2]!,
      id: "00000000-0000-4000-8000-000000000012",
      serverId: "00000000-0000-4000-8000-000000000012",
      revision: 1,
    };
    const fixture = {
      ...DEMO_FIXTURE,
      name: "Stale draft fixture",
      annotations: [{ ...saved, sku: "Horlicks Chocolate" }, added],
    };
    localStorage.setItem(
      `shelfsight:annotation-draft:${fixture.images.map((image) => image.id).join(",")}`,
      JSON.stringify({
        version: 1,
        annotations: [
          {
            id: saved.id,
            server_id: saved.serverId,
            image_id: saved.imageId,
            revision: 2,
            x: saved.x,
            y: saved.y,
            width: saved.width,
            height: saved.height,
            kind: "product",
            state: saved.state,
            lifecycle_state: saved.lifecycleState,
            review_state: saved.reviewState,
            sku: "Assigned SKU",
            sku_id: saved.skuId,
            confidence: saved.confidence,
            occluded: false,
            truncated: false,
            shelf_row: saved.shelfRow,
            image_index: 0,
          },
        ],
        pending: [],
      }),
    );

    const { container } = render(<AnnotationWorkspace fixture={fixture} />);

    expect(
      container.querySelector(`.box-row[data-annotation-id="${saved.id}"]`),
    ).toHaveTextContent("Horlicks Chocolate");
    expect(
      container.querySelector(`.box-row[data-annotation-id="${added.id}"]`),
    ).toBeInTheDocument();
    expect(screen.queryByText("Recovered local work")).not.toBeInTheDocument();
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

function proposedBoxes(count: number) {
  return DEMO_FIXTURE.annotations.slice(1, 1 + count).map((box) => ({
    ...box,
    state: "unverified" as const,
    lifecycleState: "proposed" as const,
    reviewState: "unreviewed" as const,
  }));
}

function rowIds(container: HTMLElement) {
  return [...container.querySelectorAll(".box-row")].map(
    (row) => row.getAttribute("data-annotation-id")!,
  );
}

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
    polygon: null,
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

function drag(
  viewport: HTMLElement,
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  fireEvent.pointerDown(viewport, { button: 0, pointerId: 1, clientX: from.x, clientY: from.y });
  fireEvent.pointerMove(viewport, { pointerId: 1, clientX: to.x, clientY: to.y });
  fireEvent.pointerUp(viewport, { pointerId: 1, clientX: to.x, clientY: to.y });
}
