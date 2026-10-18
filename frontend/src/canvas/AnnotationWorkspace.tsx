import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";

import { AnnotationOverlay } from "./AnnotationOverlay";
import { AutosavePanel } from "./AutosavePanel";
import { BenchmarkOutput } from "./BenchmarkOutput";
import {
  assessHost,
  measureFrameIntervals,
  nextFrame,
  summarizeTimings,
  type CanvasBenchmark,
} from "./benchmark";
import {
  acceptBox,
  assignSku,
  changeBoxClass,
  duplicateBox,
  flagBox,
  localBoxId,
  nudgeBox,
  pasteBox,
  rejectBox,
  resizeBox,
  type DrawnClass,
  type EditIntent,
} from "./editing";
import { CanvasTools, type CanvasTool } from "./CanvasTools";
import {
  canvasCommand,
  ZOOM_STEP,
  type CanvasCommand,
  type Direction,
} from "./canvasKeys";
import { copiedBoxes, copyBoxes } from "./clipboard";
import {
  hiddenBoxClasses,
  imageFilter,
  loadDisplaySettings,
  saveDisplaySettings,
  type DisplaySettings,
} from "./display";
import { DisplayPanel } from "./DisplayPanel";
import { GroupSelectionDetails, SelectionDetails } from "./SelectionDetails";
import { ShortcutHelp } from "./ShortcutHelp";
import { SkuAssignmentPanel } from "./SkuAssignmentPanel";
import type { AnnotationBox, AnnotationClass, CanvasFixture } from "./model";
import { skuLabel, spatialReadingOrder } from "./model";
import {
  addToSelection,
  rectBetween,
  selectMany,
  selectOnly,
  toggleSelected,
  type SceneRect,
  type Selection,
} from "./selection";
import {
  fitTransform,
  revealBox,
  zoomTransform,
  type SceneTransform,
} from "./transform";
import { useAnnotationAutosave, type SaveStatus } from "./useAnnotationAutosave";
import { useCanvasPointer } from "./useCanvasPointer";
import { useSkuAssignmentCatalog } from "./useSkuAssignmentCatalog";
import type { Sku } from "../catalog/model";

type AnnotationWorkspaceProps = {
  fixture: CanvasFixture;
  mode?: "verify" | "assign";
  purpose?: "annotation" | "gap-review";
  onAnnotationsChange?: (annotations: AnnotationBox[]) => void;
  onSaveStateChange?: (
    status: SaveStatus,
    flush: () => Promise<void>,
  ) => void;
};

const LIST_ROW_HEIGHT = 50;
const LIST_OVERSCAN = 5;
const HANDLE_SCREEN_SIZE = 9;
const DEFAULT_VIEWPORT = { width: 900, height: 700 };

export function AnnotationWorkspace({
  fixture,
  mode = "verify",
  purpose = "annotation",
  onAnnotationsChange,
  onSaveStateChange,
}: AnnotationWorkspaceProps) {
  const isGapReview = purpose === "gap-review";
  const autosave = useAnnotationAutosave(fixture);
  const [skuPickerOpen, setSkuPickerOpen] = useState(mode === "assign");
  const assignmentCatalog = useSkuAssignmentCatalog(
    mode === "assign" || skuPickerOpen,
  );
  const [lastAssignedSku, setLastAssignedSku] = useState<Sku | null>(null);
  const activeAnnotations = useMemo(
    () =>
      autosave.annotations.filter(
        (annotation) => annotation.lifecycleState !== "rejected",
      ),
    [autosave.annotations],
  );
  const workspaceAnnotations = useMemo(
    () =>
      mode === "assign"
        ? activeAnnotations.filter(
            (annotation) =>
              annotation.classType === "product" &&
              annotation.lifecycleState === "verified" &&
              annotation.reviewState === "accepted",
          )
        : activeAnnotations,
    [activeAnnotations, mode],
  );
  const orderedAnnotations = useMemo(
    () => spatialReadingOrder(workspaceAnnotations),
    [workspaceAnnotations],
  );
  const [selection, setSelection] = useState<Selection>(() =>
    selectOnly(
      (isGapReview
        ? orderedAnnotations.find((box) => !isResolvedAnnotation(box))
        : orderedAnnotations[0]
      )?.id ?? orderedAnnotations[0]?.id ?? null,
    ),
  );
  const selectedId = selection.primary;
  const setSelectedId = useCallback(
    (id: string | null) => setSelection(selectOnly(id)),
    [],
  );
  const [transform, setTransform] = useState<SceneTransform>({
    scale: 1,
    x: 0,
    y: 0,
  });
  const [search, setSearch] = useState("");
  const [listScrollTop, setListScrollTop] = useState(0);
  const [listViewportHeight, setListViewportHeight] = useState(900);
  const [viewportDimensions, setViewportDimensions] = useState(DEFAULT_VIEWPORT);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [tool, setTool] = useState<CanvasTool>("select");
  const drawMode: DrawnClass | null = tool === "product" || tool === "gap" ? tool : null;
  const availableTools: readonly CanvasTool[] =
    mode === "assign"
      ? ["select", "pan"]
      : isGapReview
        ? ["select", "pan", "gap"]
        : ["select", "pan", "product", "gap"];
  const [display, setDisplay] = useState<DisplaySettings>(loadDisplaySettings);
  const [hideAll, setHideAll] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [benchmark, setBenchmark] = useState<CanvasBenchmark | null>(null);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [benchmarking, setBenchmarking] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const duplicateCounterRef = useRef(0);
  const {
    panning,
    draftBox,
    draftGeometry,
    marquee,
    crosshairRef,
    cancelDrawing,
    startGeometryDrag,
    handlers: pointerHandlers,
  } = useCanvasPointer({
    fixture,
    drawMode: mode === "verify" ? drawMode : null,
    tool,
    spaceHeld,
    transform,
    setTransform,
    viewportRef,
    activeAnnotations,
    selectableAnnotations: orderedAnnotations,
    onBoxDrawn: completeDrawnBox,
    onGeometryChanged: (annotationId, geometry) =>
      autosave.edit(annotationId, "update", (box) => ({
        ...box,
        ...geometry,
        confidence: null,
      })),
    onBoxClicked: (annotationId) => selectAnnotation(annotationId),
    onMarqueeSelect: selectInside,
    onBackgroundClick: () => {
      setSelectedId(null);
      setSkuPickerOpen(false);
    },
  });

  const displayedAnnotations = useMemo(() => {
    const boxes = draftGeometry
      ? activeAnnotations.map((box) =>
          box.id === draftGeometry.id ? { ...box, ...draftGeometry, id: box.id } : box,
        )
      : activeAnnotations;
    return draftBox ? [...boxes, draftBox] : boxes;
  }, [activeAnnotations, draftBox, draftGeometry]);
  const selectedBox =
    orderedAnnotations.find((annotation) => annotation.id === selectedId) ?? null;
  const selectedBoxes = useMemo(
    () => orderedAnnotations.filter((annotation) => selection.ids.has(annotation.id)),
    [orderedAnnotations, selection.ids],
  );
  const isGroup = selectedBoxes.length > 1;
  const groupIds = useMemo(
    () => (isGroup ? new Set(selectedBoxes.map((box) => box.id)) : undefined),
    [isGroup, selectedBoxes],
  );
  const showSkuPicker =
    skuPickerOpen &&
    selectedBox?.classType === "product" &&
    selectedBox.lifecycleState !== "rejected";
  const filteredAnnotations = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) {
      return orderedAnnotations;
    }
    return orderedAnnotations.filter((box) =>
      `${box.id} ${box.sku} ${box.state} ${box.classType}`
        .toLowerCase()
        .includes(query),
    );
  }, [orderedAnnotations, search]);

  useEffect(() => {
    onAnnotationsChange?.(autosave.annotations);
  }, [autosave.annotations, onAnnotationsChange]);

  useEffect(() => {
    onSaveStateChange?.(autosave.status, autosave.flush);
  }, [autosave.flush, autosave.status, onSaveStateChange]);

  const fitView = useCallback(() => {
    const viewport = viewportSize(viewportRef.current);
    setTransform(
      fitTransform(viewport, { width: fixture.width, height: fixture.height }),
    );
  }, [fixture.height, fixture.width]);

  useEffect(() => {
    fitView();
    const handleResize = () => {
      fitView();
      setViewportDimensions(viewportSize(viewportRef.current));
      setListViewportHeight(listRef.current?.clientHeight || 900);
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [fitView]);

  useEffect(() => {
    if (!selectedBox) {
      return;
    }
    setTransform((current) =>
      revealBox(current, viewportSize(viewportRef.current), selectedBox),
    );
    const index = filteredAnnotations.findIndex((box) => box.id === selectedBox.id);
    const list = listRef.current;
    if (index < 0 || !list) {
      return;
    }
    const rowTop = index * LIST_ROW_HEIGHT;
    const rowBottom = rowTop + LIST_ROW_HEIGHT;
    if (rowTop < list.scrollTop) {
      list.scrollTop = rowTop;
    } else if (rowBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = rowBottom - list.clientHeight;
    }
  }, [filteredAnnotations, selectedBox]);

  const selectRelative = useCallback(
    (delta: number) => {
      if (orderedAnnotations.length === 0) {
        return;
      }
      const currentIndex = orderedAnnotations.findIndex((box) => box.id === selectedId);
      const nextIndex =
        currentIndex < 0
          ? 0
          : (currentIndex + delta + orderedAnnotations.length) %
            orderedAnnotations.length;
      setSelectedId(orderedAnnotations[nextIndex]!.id);
    },
    [orderedAnnotations, selectedId, setSelectedId],
  );

  function selectAnnotation(annotationId: string, extend = false) {
    if (extend) {
      setSelection((current) => toggleSelected(current, annotationId));
      if (mode === "verify") {
        setSkuPickerOpen(false);
      }
      return;
    }
    setSelectedId(annotationId);
    const annotation = activeAnnotations.find((box) => box.id === annotationId);
    setSkuPickerOpen(annotation?.classType === "product");
  }

  useEffect(() => {
    if (showSkuPicker) {
      assignmentCatalog.searchInputRef.current?.focus();
    }
  }, [assignmentCatalog.searchInputRef, showSkuPicker]);

  // Verification opens on its first box, so shortcuts work before any click.
  useEffect(() => {
    if (mode === "verify") {
      viewportRef.current?.focus({ preventScroll: true });
    }
  }, [mode]);

  function zoomBy(multiplier: number) {
    const viewport = viewportSize(viewportRef.current);
    setTransform((current) =>
      zoomTransform(current, current.scale * multiplier, {
        x: viewport.width / 2,
        y: viewport.height / 2,
      }),
    );
  }

  function stepHistory(direction: "undo" | "redo") {
    const moved = direction === "undo" ? autosave.undo() : autosave.redo();
    if (moved.length === 0) {
      return;
    }
    // Selecting the changed boxes pans to them, so the reviewer sees what was undone.
    setSelection(
      selectMany(
        moved.filter((box) => box.lifecycleState !== "rejected").map((box) => box.id),
      ),
    );
    if (mode === "verify") {
      closeSkuPicker();
    }
  }

  // One action over every selected box, so a group decision undoes in one step.
  function editSelection(
    intent: EditIntent,
    update: (box: AnnotationBox) => AnnotationBox,
  ) {
    autosave.action(() => {
      for (const box of selectedBoxes) {
        autosave.edit(box.id, intent, update);
      }
    });
  }

  function addBoxes(boxes: AnnotationBox[]) {
    autosave.action(() => {
      for (const box of boxes) {
        autosave.add(box);
      }
    });
    setSelection(selectMany(boxes.map((box) => box.id)));
  }

  function acceptSelected() {
    if (isGroup) {
      editSelection("accept", acceptBox);
      return;
    }
    if (!selectedId || !selectedBox || isResolvedAnnotation(selectedBox)) {
      return;
    }
    autosave.edit(selectedId, "accept", acceptBox);
    moveToNextDecision(selectedId);
  }

  // Every decision moves on to the next undecided box, so a reviewer can work through a
  // shelf with repeated A presses or Accept clicks instead of selecting each box.
  function moveToNextDecision(fromId: string) {
    setSelectedId(nextUnresolvedId(orderedAnnotations, fromId));
    closeSkuPicker();
  }

  function flagSelected() {
    editSelection("update", flagBox);
  }

  function rejectSelected() {
    if (isGroup) {
      editSelection("reject", rejectBox);
      setSelectedId(null);
      closeSkuPicker();
      return;
    }
    if (!selectedId) {
      return;
    }
    autosave.edit(selectedId, "reject", rejectBox);
    if (drawMode) {
      // While drawing, removing a box leaves nothing selected instead of panning away.
      setSelectedId(null);
      closeSkuPicker();
    } else {
      moveToNextDecision(selectedId);
    }
  }

  function duplicateSelected() {
    if (selectedBoxes.length === 0) {
      return;
    }
    addBoxes(
      selectedBoxes.map((box) => {
        duplicateCounterRef.current += 1;
        return duplicateBox(box, `${box.id}-copy-${duplicateCounterRef.current}`, fixture);
      }),
    );
  }

  // A copy outlives this workspace, so it can be pasted into the next photo.
  function copySelection() {
    copyBoxes({ boxes: selectedBoxes, images: fixture.images });
  }

  function pasteCopies() {
    const copied = copiedBoxes();
    if (!copied) {
      return;
    }
    const batch = localBoxId("pasted");
    const pasted = copied.boxes
      .filter((box) => !isGapReview || box.classType === "gap")
      .map((box, index) =>
        pasteBox(box, `${batch}-${index}`, copied.images[box.imageIndex], fixture.images),
      );
    if (pasted.length === 0) {
      return;
    }
    addBoxes(pasted);
    closeSkuPicker();
  }

  function selectAll() {
    setSelection(selectMany(orderedAnnotations.map((box) => box.id)));
    if (mode === "verify") {
      closeSkuPicker();
    }
  }

  function changeDisplay(next: DisplaySettings) {
    setDisplay(next);
    saveDisplaySettings(next);
  }

  function closeHelp() {
    setHelpOpen(false);
    viewportRef.current?.focus();
  }

  function completeDrawnBox(completed: AnnotationBox) {
    setSelectedId(completed.id);
    if (completed.classType === "product" && lastAssignedSku) {
      // Shelves repeat products, so a new box takes the previous SKU through the same
      // accept and assign steps as the picker; / changes it.
      const sku = lastAssignedSku;
      autosave.action(() => {
        autosave.add(completed);
        autosave.edit(completed.id, "accept", acceptBox);
        autosave.edit(completed.id, "assign", (box) => assignSku(box, sku.id, sku.name));
      });
      closeSkuPicker();
    } else {
      autosave.add(completed);
      // A drawn product needs its SKU next, so the picker opens on it straight away.
      setSkuPickerOpen(completed.classType === "product");
    }
  }

  function selectInside(ids: string[], extend: boolean) {
    setSelection((current) => (extend ? addToSelection(current, ids) : selectMany(ids)));
    if (mode === "verify") {
      setSkuPickerOpen(false);
    }
  }

  function changeSelectedShelfRow(shelfRow: number | null) {
    if (!selectedId) {
      return;
    }
    autosave.edit(selectedId, "update", (box) => ({ ...box, shelfRow }));
  }

  function changeSelectedClass(classType: AnnotationClass) {
    editSelection("update", (box) => changeBoxClass(box, classType));
    setSkuPickerOpen(!isGroup && classType === "product");
  }

  // Naming accepts each box first, because only accepted products may carry a SKU.
  function assignSelectedSku(sku: Sku) {
    const targets = selectedBoxes.filter((box) => box.classType === "product");
    if (!selectedId || targets.length === 0) {
      return;
    }
    const currentIndex = orderedAnnotations.findIndex(
      (annotation) => annotation.id === selectedId,
    );
    autosave.action(() => {
      for (const target of targets) {
        autosave.edit(target.id, "accept", acceptBox);
        autosave.edit(target.id, "assign", (box) => assignSku(box, sku.id, sku.name));
      }
    });
    assignmentCatalog.remember(sku);
    setLastAssignedSku(sku);
    if (targets.length > 1) {
      // A group stays selected, so the reviewer sees every box it named.
      if (mode === "verify") {
        closeSkuPicker();
      }
    } else if (mode === "assign") {
      const next = orderedAnnotations[currentIndex + 1];
      if (next) {
        setSelectedId(next.id);
      }
    } else if (drawMode === "product") {
      // Stay on the box just named instead of jumping (and panning) elsewhere.
      closeSkuPicker();
    } else {
      moveToNextDecision(selectedId);
    }
  }

  // The verify picker is a popover over the shelf, so it closes once used and hands the
  // keyboard back to the canvas shortcuts.
  function closeSkuPicker() {
    setSkuPickerOpen(false);
    viewportRef.current?.focus();
  }

  // Pressing a drawing tool's key again returns to Select, as the old toggle did.
  function chooseTool(next: CanvasTool) {
    if (!availableTools.includes(next)) {
      return;
    }
    setTool((current) => (current === next && next !== "select" ? "select" : next));
    cancelDrawing();
  }

  function editGeometry(
    direction: Direction,
    resize: boolean,
  ) {
    if (!selectedId || isGroup) {
      return;
    }
    const deltaX = direction === "left" ? -1 : direction === "right" ? 1 : 0;
    const deltaY = direction === "up" ? -1 : direction === "down" ? 1 : 0;
    autosave.edit(selectedId, "update", (box) =>
      resize
        ? resizeBox(box, deltaX, deltaY, fixture)
        : nudgeBox(box, deltaX, deltaY, fixture),
    );
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (isEditableTarget(event.target)) {
      return;
    }
    if (event.code === "Space") {
      event.preventDefault();
      setSpaceHeld(true);
      return;
    }
    const command = canvasCommand(event, {
      mode,
      candidateCount: assignmentCatalog.candidates.length,
      hasUnknownSku: assignmentCatalog.unknownSku !== null,
      hasPreviousSku: lastAssignedSku !== null,
      selectedIsProduct: selectedBox?.classType === "product",
      hasSelection: selectedBoxes.length > 0,
      hasCopy: copiedBoxes() !== null,
    });
    if (command) {
      event.preventDefault();
      runCommand(command);
    }
  }

  // canvasCommand asks for an assignment only when its SKU exists, so the lookups below
  // always find one.
  function runCommand(command: CanvasCommand) {
    switch (command.type) {
      case "undo":
      case "redo":
        return stepHistory(command.type);
      case "selectAll":
        return selectAll();
      case "copy":
        return copySelection();
      case "paste":
        return pasteCopies();
      case "assignCandidate":
        return assignSelectedSku(assignmentCatalog.candidates[command.index]!);
      case "assignUnknown":
        return assignSelectedSku(assignmentCatalog.unknownSku!);
      case "assignPrevious":
        return assignSelectedSku(lastAssignedSku!);
      case "searchCatalog":
        return assignmentCatalog.searchInputRef.current?.focus();
      case "editGeometry":
        return editGeometry(command.direction, command.resize);
      case "accept":
        return acceptSelected();
      case "reject":
        return rejectSelected();
      case "changeSku":
        setSkuPickerOpen(true);
        return assignmentCatalog.searchInputRef.current?.focus();
      case "flag":
        return flagSelected();
      case "duplicate":
        return duplicateSelected();
      case "showShortcuts":
        return setHelpOpen(true);
      case "toggleBoxes":
        return setHideAll((current) => !current);
      case "chooseTool":
        return chooseTool(command.tool);
      case "step":
        return selectRelative(command.delta);
      case "clear":
        setTool("select");
        cancelDrawing();
        setSelectedId(null);
        return setSkuPickerOpen(false);
      case "zoom":
        return zoomBy(command.factor);
    }
  }

  async function runBenchmark() {
    if (benchmarking || orderedAnnotations.length === 0) {
      return;
    }
    setBenchmarking(true);
    setBenchmark(null);
    setBenchmarkError(null);
    const originalSelection = selectedId;
    try {
      const browserFrameBaseline = await measureFrameIntervals(60);
      const annotationNodes = Array.from(
        viewportRef.current?.querySelectorAll<SVGGElement>(".annotation") ?? [],
      );
      const frameIntervals = await measureFrameIntervals(
        90,
        (index, active) => {
          const annotation =
            annotationNodes[(index * 19) % annotationNodes.length];
          annotation?.classList.toggle("benchmark-pulse", active);
        },
      );
      const selectionUpdates: number[] = [];
      for (let index = 0; index < 200; index += 1) {
        const annotation = orderedAnnotations[index % orderedAnnotations.length]!;
        const startedAt = performance.now();
        flushSync(() => setSelectedId(annotation.id));
        selectionUpdates.push(performance.now() - startedAt);
        if ((index + 1) % 5 === 0) {
          await nextFrame();
        }
      }

      const inputLatencies: number[] = [];
      for (let index = 0; index < 45; index += 1) {
        const annotation = orderedAnnotations[index % orderedAnnotations.length]!;
        const startedAt = performance.now();
        flushSync(() => setSelectedId(annotation.id));
        await nextFrame();
        inputLatencies.push(performance.now() - startedAt);
      }
      const baselineSummary = summarizeTimings(browserFrameBaseline);
      const frameSummary = summarizeTimings(frameIntervals);
      setBenchmark({
        browserFrameBaseline: baselineSummary,
        frameInterval: frameSummary,
        selectionUpdate: summarizeTimings(selectionUpdates),
        inputLatency: summarizeTimings(inputLatencies),
        measuredAt: new Date().toISOString(),
        host: assessHost(baselineSummary, frameSummary),
      });
    } catch {
      setBenchmarkError("Measurement failed. Reload the page and try again.");
    } finally {
      setSelectedId(originalSelection);
      setBenchmarking(false);
    }
  }

  const visibleStart = Math.max(
    0,
    Math.floor(listScrollTop / LIST_ROW_HEIGHT) - LIST_OVERSCAN,
  );
  const visibleEnd = Math.min(
    filteredAnnotations.length,
    visibleStart +
      Math.ceil(listViewportHeight / LIST_ROW_HEIGHT) +
      LIST_OVERSCAN * 2,
  );
  const visibleRows = filteredAnnotations.slice(visibleStart, visibleEnd);

  return (
    <main className="annotation-workspace">
      <aside className="box-panel" aria-label="Annotation list">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">
              {isGapReview
                ? "Visible gaps"
                : mode === "assign"
                  ? "Verified products"
                  : "Facings"}
            </span>
            <strong>{filteredAnnotations.length} shown</strong>
          </div>
          <span className="panel-count">{activeAnnotations.length}</span>
        </div>
        <label className="search-row">
          <span>Search boxes</span>
          <input
            type="search"
            value={search}
            placeholder="SKU, class, state, or ID"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <div
          className="box-list"
          ref={listRef}
          role="listbox"
          aria-label="Boxes in spatial reading order"
          aria-multiselectable="true"
          onScroll={(event) => setListScrollTop(event.currentTarget.scrollTop)}
        >
          <div
            className="box-list-spacer"
            style={{ height: filteredAnnotations.length * LIST_ROW_HEIGHT }}
          >
            {visibleRows.map((box, visibleIndex) => {
              const index = visibleStart + visibleIndex;
              return (
                <button
                  type="button"
                  className={`box-row${selection.ids.has(box.id) ? " is-selected" : ""}`}
                  style={{ top: index * LIST_ROW_HEIGHT }}
                  data-annotation-id={box.id}
                  role="option"
                  aria-selected={selection.ids.has(box.id)}
                  onClick={(event) =>
                    selectAnnotation(box.id, event.shiftKey || event.ctrlKey || event.metaKey)
                  }
                  key={box.id}
                >
                  <span className="state-marker" data-state={box.state}>
                    {stateSymbol(box.state)}
                  </span>
                  <span className="box-copy">
                    <strong>{skuLabel(box)}</strong>
                    <span>
                      {box.id} · {box.classType.replace("_", " ")}
                    </span>
                  </span>
                  <span className="box-confidence">
                    {box.confidence === null
                      ? "N/A"
                      : `${Math.round(box.confidence * 100)}%`}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </aside>

      <section className="canvas-column">
        <div className="canvas-context">
          <span>
            {isGapReview ? "Gap review" : "Shelf photo"}
          </span>
          <span>{fixture.name}</span>
          {(hideAll || display.hiddenClasses.length > 0) && <span>Some boxes hidden</span>}
        </div>
        <div
          className={`canvas-viewport tool-${tool}${panning ? " is-panning" : ""}${
            spaceHeld || tool === "pan" ? " is-pan-ready" : ""
          }${drawMode ? ` is-drawing is-drawing-${drawMode}` : ""}`}
          ref={viewportRef}
          tabIndex={0}
          aria-label="Shelf annotation canvas"
          onKeyDown={handleKeyDown}
          onKeyUp={(event) => {
            if (event.code === "Space") {
              setSpaceHeld(false);
            }
          }}
          onBlur={() => setSpaceHeld(false)}
          {...pointerHandlers}
        >
          <div
            className={`canvas-scene${hiddenBoxClasses(display, hideAll)}`}
            style={
              {
                width: fixture.width,
                height: fixture.height,
                transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
                "--ov-fill-op": display.boxFill / 100,
              } as React.CSSProperties
            }
          >
            <div
              className="image-layer"
              aria-label="Shelf image"
              style={{ filter: imageFilter(display) }}
            >
              {fixture.images.map((image) => (
                <img
                  src={image.url}
                  alt=""
                  draggable={false}
                  style={{
                    left: image.x,
                    top: image.y,
                    width: image.width,
                    height: image.height,
                  }}
                  key={image.id}
                />
              ))}
            </div>
            <AnnotationOverlay
              annotations={displayedAnnotations}
              width={fixture.width}
              height={fixture.height}
              selectedId={isGroup ? null : selectedId}
              groupIds={groupIds}
              interactive={!spaceHeld && tool === "select"}
              handleSize={HANDLE_SCREEN_SIZE / transform.scale}
              handlesActive={!spaceHeld && tool !== "pan"}
              onSelect={selectAnnotation}
              onGeometryStart={startGeometryDrag}
            />
          </div>
          {drawMode && (
            <div className="draw-crosshair" ref={crosshairRef} aria-hidden="true" />
          )}
          {marquee?.moved && (
            <div
              className="selection-marquee"
              aria-hidden="true"
              style={screenRect(
                rectBetween(marquee.start, marquee.end),
                transform,
              )}
            />
          )}
          {showSkuPicker && selectedBox && (
            <div
              className="sku-picker-popover"
              style={skuPickerPosition(
                selectedBox,
                transform,
                viewportDimensions,
              )}
            >
              <SkuAssignmentPanel
                box={selectedBox}
                targetCount={selectedBoxes.filter((box) => box.classType === "product").length}
                candidates={assignmentCatalog.candidates}
                query={assignmentCatalog.query}
                recentSkus={assignmentCatalog.recentSkus}
                unknownSku={assignmentCatalog.unknownSku}
                lastAssignedSku={lastAssignedSku}
                source={assignmentCatalog.source}
                searchInputRef={assignmentCatalog.searchInputRef}
                variant="popover"
                onQueryChange={assignmentCatalog.setQuery}
                onAssign={assignSelectedSku}
                onClose={() => {
                  setSelectedId(null);
                  closeSkuPicker();
                }}
                onRemove={mode === "verify" && !isGroup ? rejectSelected : undefined}
              />
            </div>
          )}
          <div className="zoom-controls" aria-label="Canvas zoom controls">
            <button type="button" aria-label="Zoom in" onClick={() => zoomBy(ZOOM_STEP)}>
              +
            </button>
            <output aria-label="Zoom level">{Math.round(transform.scale * 100)}%</output>
            <button type="button" aria-label="Zoom out" onClick={() => zoomBy(1 / ZOOM_STEP)}>
              −
            </button>
            <button type="button" onClick={fitView}>
              Fit
            </button>
          </div>
          <CanvasTools
            tool={tool}
            available={availableTools}
            onChange={chooseTool}
            history={{
              canUndo: autosave.canUndo,
              canRedo: autosave.canRedo,
              onUndo: () => stepHistory("undo"),
              onRedo: () => stepHistory("redo"),
            }}
          />
          <div className="shortcut-strip">
            <span>
              {mode === "assign"
                ? "1-9 assign · S same as previous · U unknown"
                : `A accept · R reject · F flag${isGapReview ? "" : " · B box"} · G gap`}
            </span>
            <span>Ctrl+Z undo</span>
            <button type="button" onClick={() => setHelpOpen(true)}>
              <kbd>?</kbd> All shortcuts
            </button>
          </div>
        </div>
      </section>

      <aside className="detail-panel" aria-label="Selection details">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">
              {isGroup ? "Selection" : isGapReview ? "Selected gap" : "Selected facing"}
            </span>
            <strong>{isGroup ? `${selectedBoxes.length} boxes` : (selectedBox?.id ?? "None")}</strong>
          </div>
          <span className="panel-count">
            {isGroup
              ? selectedBoxes.length
              : selectedBox
                ? orderedAnnotations.indexOf(selectedBox) + 1
                : 0}{" "}
            / {orderedAnnotations.length}
          </span>
        </div>
        {isGroup ? (
          <GroupSelectionDetails
            boxes={selectedBoxes}
            onAccept={acceptSelected}
            onReject={rejectSelected}
            onFlag={flagSelected}
            onDuplicate={duplicateSelected}
            onClassChange={changeSelectedClass}
            showVerificationActions={mode === "verify"}
            lockGapClass={isGapReview}
          />
        ) : selectedBox ? (
          <SelectionDetails
            fixture={fixture}
            box={selectedBox}
            onAccept={acceptSelected}
            onReject={rejectSelected}
            onFlag={flagSelected}
            onDuplicate={duplicateSelected}
            onShelfRowChange={changeSelectedShelfRow}
            onClassChange={changeSelectedClass}
            showVerificationActions={mode === "verify"}
            lockGapClass={isGapReview}
          />
        ) : (
          <p className="empty-state">
            {isGapReview
              ? "No gap selected. Empty shelf-space boxes are the review target."
              : "Choose a box on the canvas or in the list."}
          </p>
        )}
        <AutosavePanel
          mode={autosave.mode}
          onModeChange={autosave.setMode}
          status={autosave.status}
          message={autosave.message}
          onSave={() => void autosave.flush()}
          conflict={autosave.conflict}
          onUseServer={autosave.useServerVersion}
          onKeepLocal={autosave.keepLocalVersion}
        />
        <DisplayPanel
          settings={display}
          hideAll={hideAll}
          onChange={changeDisplay}
          onHideAllChange={setHideAll}
        />
        {mode === "verify" && (
          <section className="performance-panel" aria-label="Canvas performance">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">Performance</span>
                <strong>Production canvas check</strong>
              </div>
              <button
                type="button"
                onClick={() => void runBenchmark()}
                disabled={benchmarking}
              >
                {benchmarking ? "Measuring" : "Measure"}
              </button>
            </div>
            <BenchmarkOutput benchmark={benchmark} error={benchmarkError} />
          </section>
        )}
      </aside>
      {helpOpen && <ShortcutHelp context={{ mode, isGapReview }} onClose={closeHelp} />}
    </main>
  );
}

function stateSymbol(state: AnnotationBox["state"]): string {
  const symbols: Record<AnnotationBox["state"], string> = {
    unverified: "?",
    verified: "✓",
    propagated: "⇉",
    flagged: "!",
  };
  return symbols[state];
}

function viewportSize(element: HTMLDivElement | null) {
  if (!element) {
    return DEFAULT_VIEWPORT;
  }
  const bounds = element.getBoundingClientRect();
  return {
    width: bounds.width || element.clientWidth || DEFAULT_VIEWPORT.width,
    height: bounds.height || element.clientHeight || DEFAULT_VIEWPORT.height,
  };
}

function skuPickerPosition(
  box: AnnotationBox,
  transform: SceneTransform,
  viewport: { width: number; height: number },
) {
  const width = 340;
  const estimatedHeight = 520;
  const gap = 12;
  const padding = 12;
  const boxLeft = transform.x + box.x * transform.scale;
  const boxRight = transform.x + (box.x + box.width) * transform.scale;
  const boxTop = transform.y + box.y * transform.scale;
  const preferredLeft = boxRight + gap;
  const left =
    preferredLeft + width <= viewport.width - padding
      ? preferredLeft
      : boxLeft - width - gap;
  return {
    left: Math.max(padding, Math.min(left, viewport.width - width - padding)),
    top: Math.max(
      padding,
      Math.min(boxTop, viewport.height - estimatedHeight - padding),
    ),
  };
}

function screenRect(rect: SceneRect, transform: SceneTransform) {
  return {
    left: transform.x + rect.x * transform.scale,
    top: transform.y + rect.y * transform.scale,
    width: rect.width * transform.scale,
    height: rect.height * transform.scale,
  };
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}

function isResolvedAnnotation(box: AnnotationBox): boolean {
  return (
    box.lifecycleState === "rejected" ||
    (box.lifecycleState === "verified" && box.reviewState === "accepted")
  );
}

function nextUnresolvedId(
  annotations: AnnotationBox[],
  currentId: string,
): string | null {
  const currentIndex = annotations.findIndex((box) => box.id === currentId);
  const ordered = [
    ...annotations.slice(currentIndex + 1),
    ...annotations.slice(0, Math.max(currentIndex, 0)),
  ];
  return ordered.find((box) => !isResolvedAnnotation(box))?.id ?? null;
}
