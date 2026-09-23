import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";

import { AnnotationOverlay } from "./AnnotationOverlay";
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
  createDrawnBox,
  duplicateBox,
  flagBox,
  nudgeBox,
  rejectBox,
  resizeBox,
  type DrawnClass,
} from "./editing";
import { SkuAssignmentPanel } from "./SkuAssignmentPanel";
import type { AnnotationBox, AnnotationClass, CanvasFixture } from "./model";
import { overlayCue, spatialReadingOrder } from "./model";
import {
  fitTransform,
  revealBox,
  zoomTransform,
  type SceneTransform,
} from "./transform";
import {
  useAnnotationAutosave,
  type AnnotationConflict,
  type AutosaveMode,
  type SaveStatus,
} from "./useAnnotationAutosave";
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

type PanStart = {
  pointerId: number;
  clientX: number;
  clientY: number;
  transformX: number;
  transformY: number;
};

type DrawStart = {
  pointerId: number;
  classType: DrawnClass;
  id: string;
  imageIndex: number;
  shelfRow: number;
  x: number;
  y: number;
};

const LIST_ROW_HEIGHT = 50;
const LIST_OVERSCAN = 5;
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
  const [selectedId, setSelectedId] = useState<string | null>(
    (isGapReview
      ? orderedAnnotations.find((box) => !isResolvedAnnotation(box))
      : orderedAnnotations[0]
    )?.id ?? orderedAnnotations[0]?.id ?? null,
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
  const [panStart, setPanStart] = useState<PanStart | null>(null);
  const [drawMode, setDrawMode] = useState<DrawnClass | null>(null);
  const [drawStart, setDrawStart] = useState<DrawStart | null>(null);
  const [draftBox, setDraftBox] = useState<AnnotationBox | null>(null);
  const [benchmark, setBenchmark] = useState<CanvasBenchmark | null>(null);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [benchmarking, setBenchmarking] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const duplicateCounterRef = useRef(0);
  const drawCounterRef = useRef(0);

  const selectedBox =
    orderedAnnotations.find((annotation) => annotation.id === selectedId) ?? null;
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
    [orderedAnnotations, selectedId],
  );

  function selectAnnotation(annotationId: string) {
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

  function acceptSelected() {
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
    if (selectedId) {
      autosave.edit(selectedId, "update", flagBox);
    }
  }

  function rejectSelected() {
    if (!selectedId) {
      return;
    }
    autosave.edit(selectedId, "reject", rejectBox);
    moveToNextDecision(selectedId);
  }

  function duplicateSelected() {
    if (!selectedBox) {
      return;
    }
    duplicateCounterRef.current += 1;
    const duplicate = duplicateBox(
      selectedBox,
      `${selectedBox.id}-copy-${duplicateCounterRef.current}`,
      fixture,
    );
    autosave.add(duplicate);
    setSelectedId(duplicate.id);
  }

  function changeSelectedShelfRow(shelfRow: number | null) {
    if (!selectedId) {
      return;
    }
    autosave.edit(selectedId, "update", (box) => ({ ...box, shelfRow }));
  }

  function changeSelectedClass(classType: AnnotationClass) {
    if (!selectedId) {
      return;
    }
    autosave.edit(selectedId, "update", (box) =>
      changeBoxClass(box, classType),
    );
    setSkuPickerOpen(classType === "product");
  }

  function assignSelectedSku(sku: Sku) {
    if (!selectedId || !selectedBox) {
      return;
    }
    const needsAcceptance =
      selectedBox.lifecycleState !== "verified" ||
      selectedBox.reviewState !== "accepted";
    const assignable = needsAcceptance ? acceptBox(selectedBox) : selectedBox;
    const updated = assignSku(assignable, sku.id, sku.name);
    if (updated === selectedBox) {
      return;
    }
    const currentIndex = orderedAnnotations.findIndex(
      (annotation) => annotation.id === selectedId,
    );
    if (needsAcceptance) {
      autosave.edit(selectedId, "accept", acceptBox);
    }
    autosave.edit(selectedId, "assign", (box) =>
      assignSku(box, sku.id, sku.name),
    );
    assignmentCatalog.remember(sku);
    setLastAssignedSku(sku);
    if (mode === "assign") {
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

  function toggleDrawMode(classType: DrawnClass) {
    setDrawMode((active) => (active === classType ? null : classType));
    setDrawStart(null);
    setDraftBox(null);
  }

  function editGeometry(
    direction: "left" | "right" | "up" | "down",
    resize: boolean,
  ) {
    if (!selectedId) {
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
    const key = event.key.toLowerCase();
    const candidateIndex = Number(key) - 1;
    if (
      mode === "assign" &&
      Number.isInteger(candidateIndex) &&
      candidateIndex >= 0 &&
      candidateIndex < assignmentCatalog.candidates.length
    ) {
      event.preventDefault();
      assignSelectedSku(assignmentCatalog.candidates[candidateIndex]!);
    } else if (mode === "assign" && key === "u" && assignmentCatalog.unknownSku) {
      event.preventDefault();
      assignSelectedSku(assignmentCatalog.unknownSku);
    } else if (mode === "assign" && key === "s" && lastAssignedSku) {
      event.preventDefault();
      assignSelectedSku(lastAssignedSku);
    } else if (mode === "assign" && key === "/") {
      event.preventDefault();
      assignmentCatalog.searchInputRef.current?.focus();
    } else if (
      mode === "verify" &&
      event.altKey &&
      (key === "arrowright" ||
        key === "arrowleft" ||
        key === "arrowup" ||
        key === "arrowdown")
    ) {
      event.preventDefault();
      editGeometry(key.replace("arrow", "") as "left" | "right" | "up" | "down", event.shiftKey);
    } else if (mode === "verify" && key === "a") {
      event.preventDefault();
      acceptSelected();
    } else if (mode === "verify" && key === "r") {
      event.preventDefault();
      rejectSelected();
    } else if (mode === "verify" && key === "f") {
      event.preventDefault();
      flagSelected();
    } else if (mode === "verify" && key === "d") {
      event.preventDefault();
      duplicateSelected();
    } else if (mode === "verify" && key === "b" && !isGapReview) {
      event.preventDefault();
      toggleDrawMode("product");
    } else if (mode === "verify" && key === "g") {
      event.preventDefault();
      toggleDrawMode("gap");
    } else if (key === "arrowright" || key === "j") {
      event.preventDefault();
      selectRelative(1);
    } else if (key === "arrowleft" || key === "k") {
      event.preventDefault();
      selectRelative(-1);
    } else if (key === "escape") {
      setDrawMode(null);
      setDrawStart(null);
      setDraftBox(null);
      setSelectedId(null);
      setSkuPickerOpen(false);
    } else if (key === "+" || key === "=") {
      event.preventDefault();
      zoomBy(1.2);
    } else if (key === "-") {
      event.preventDefault();
      zoomBy(1 / 1.2);
    }
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    const shouldPan = event.button === 1 || (event.button === 0 && spaceHeld);
    if (
      mode === "verify" &&
      drawMode &&
      event.button === 0 &&
      !spaceHeld &&
      !isControlTarget(event.target)
    ) {
      event.preventDefault();
      const point = scenePoint(event, transform);
      const imageIndex = imageIndexAtPoint(fixture, point);
      if (imageIndex < 0) {
        return;
      }
      event.currentTarget.setPointerCapture(event.pointerId);
      drawCounterRef.current += 1;
      const start: DrawStart = {
        pointerId: event.pointerId,
        classType: drawMode,
        id: `${drawMode}-${Date.now()}-${drawCounterRef.current}`,
        imageIndex,
        shelfRow: nearestShelfRow(activeAnnotations, point.y),
        ...point,
      };
      setDrawStart(start);
      setDraftBox(drawnBox(start, { x: point.x + 4, y: point.y + 4 }));
      return;
    }
    if (!shouldPan) {
      if (event.button === 0 && event.target === event.currentTarget) {
        setSelectedId(null);
        setSkuPickerOpen(false);
      }
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanStart({
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      transformX: transform.x,
      transformY: transform.y,
    });
  }

  function drawnBox(start: DrawStart, end: { x: number; y: number }) {
    return createDrawnBox(
      start.classType,
      start.id,
      fixture.images[start.imageIndex]!.id,
      start.imageIndex,
      start,
      end,
      fixture,
      start.shelfRow,
    );
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (drawStart?.pointerId === event.pointerId) {
      setDraftBox(drawnBox(drawStart, scenePoint(event, transform)));
      return;
    }
    if (!panStart || panStart.pointerId !== event.pointerId) {
      return;
    }
    setTransform((current) => ({
      ...current,
      x: panStart.transformX + event.clientX - panStart.clientX,
      y: panStart.transformY + event.clientY - panStart.clientY,
    }));
  }

  function finishPointerAction(event: React.PointerEvent<HTMLDivElement>) {
    if (drawStart?.pointerId === event.pointerId) {
      const completed = drawnBox(drawStart, scenePoint(event, transform));
      if (completed) {
        autosave.add(completed);
        setSelectedId(completed.id);
        // A drawn product needs its SKU next, so the picker opens on it straight away.
        setSkuPickerOpen(completed.classType === "product");
      }
      setDrawStart(null);
      setDraftBox(null);
      return;
    }
    if (panStart?.pointerId === event.pointerId) {
      setPanStart(null);
    }
  }

  function handleWheel(event: React.WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const multiplier = Math.exp(-event.deltaY * 0.0015);
    setTransform((current) =>
      zoomTransform(current, current.scale * multiplier, {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      }),
    );
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
                  className={`box-row${box.id === selectedId ? " is-selected" : ""}`}
                  style={{ top: index * LIST_ROW_HEIGHT }}
                  data-annotation-id={box.id}
                  role="option"
                  aria-selected={box.id === selectedId}
                  onClick={() => selectAnnotation(box.id)}
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
        </div>
        <div
          className={`canvas-viewport${panStart ? " is-panning" : ""}${
            spaceHeld ? " is-pan-ready" : ""
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
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishPointerAction}
          onPointerCancel={finishPointerAction}
          onWheel={handleWheel}
        >
          <div
            className="canvas-scene"
            style={{
              width: fixture.width,
              height: fixture.height,
              transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            }}
          >
            <div className="image-layer" aria-label="Shelf image">
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
              annotations={draftBox ? [...activeAnnotations, draftBox] : activeAnnotations}
              width={fixture.width}
              height={fixture.height}
              selectedId={selectedId}
              interactive={!spaceHeld && !drawMode}
              onSelect={selectAnnotation}
            />
          </div>
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
              />
            </div>
          )}
          <div className="zoom-controls" aria-label="Canvas zoom controls">
            <button type="button" aria-label="Zoom in" onClick={() => zoomBy(1.2)}>
              +
            </button>
            <output aria-label="Zoom level">{Math.round(transform.scale * 100)}%</output>
            <button type="button" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.2)}>
              −
            </button>
            <button type="button" onClick={fitView}>
              Fit
            </button>
          </div>
          {mode === "verify" && (
            <div className="draw-controls">
              {!isGapReview && (
                <button
                  type="button"
                  className={`draw-control${drawMode === "product" ? " is-active" : ""}`}
                  aria-pressed={drawMode === "product"}
                  onClick={() => toggleDrawMode("product")}
                >
                  {drawMode === "product" ? "Drawing boxes" : "Draw box"} <kbd>B</kbd>
                </button>
              )}
              <button
                type="button"
                className={`draw-control${drawMode === "gap" ? " is-active" : ""}`}
                aria-pressed={drawMode === "gap"}
                onClick={() => toggleDrawMode("gap")}
              >
                {drawMode === "gap" ? "Drawing gaps" : "Draw gap"} <kbd>G</kbd>
              </button>
            </div>
          )}
          <div className="shortcut-strip">
            {mode === "assign" ? (
              <>
                <span>1-9 assign candidate</span>
                <span>S same as previous · U unknown</span>
                <span>/ search</span>
              </>
            ) : (
              <>
                <span>A accept · R reject · F flag · D duplicate</span>
                <span>{isGapReview ? "G draw gap" : "B draw box · G draw gap"} · Esc stop</span>
                <span>Alt + arrows nudge · add Shift to resize</span>
              </>
            )}
            <span>J K next or previous</span>
            <span>+ − zoom</span>
            <span>Space drag pans</span>
          </div>
        </div>
      </section>

      <aside className="detail-panel" aria-label="Selection details">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">
              {isGapReview ? "Selected gap" : "Selected facing"}
            </span>
            <strong>{selectedBox?.id ?? "None"}</strong>
          </div>
          <span className="panel-count">
            {selectedBox ? orderedAnnotations.indexOf(selectedBox) + 1 : 0} /{" "}
            {orderedAnnotations.length}
          </span>
        </div>
        {selectedBox ? (
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
    </main>
  );
}

function SelectionDetails({
  fixture,
  box,
  onAccept,
  onReject,
  onFlag,
  onDuplicate,
  onShelfRowChange,
  onClassChange,
  showVerificationActions,
  lockGapClass,
}: {
  fixture: CanvasFixture;
  box: AnnotationBox;
  onAccept: () => void;
  onReject: () => void;
  onFlag: () => void;
  onDuplicate: () => void;
  onShelfRowChange: (shelfRow: number | null) => void;
  onClassChange: (classType: AnnotationClass) => void;
  showVerificationActions: boolean;
  lockGapClass: boolean;
}) {
  const sourceImage = fixture.images[box.imageIndex]!;
  const backgroundScale = Math.max(2, Math.min(5, 150 / Math.max(box.width, 30)));
  const centerX = box.x - sourceImage.x + box.width / 2;
  const centerY = box.y - sourceImage.y + box.height / 2;
  return (
    <div className="selection-details">
      <div
        className="selected-crop"
        aria-label="Selected product crop"
        style={{
          backgroundImage: `url("${sourceImage.url}")`,
          backgroundSize: `${sourceImage.width * backgroundScale}px ${
            sourceImage.height * backgroundScale
          }px`,
          backgroundPosition: `${-centerX * backgroundScale + 140}px ${
            -centerY * backgroundScale + 80
          }px`,
        }}
      />
      {showVerificationActions && (
        <div className="verification-actions" aria-label="Box verification actions">
          <button
            type="button"
            className="primary-action"
            onClick={onAccept}
            disabled={
              box.lifecycleState === "verified" && box.reviewState === "accepted"
            }
          >
            {box.lifecycleState === "verified" && box.reviewState === "accepted"
              ? "Accepted"
              : "Accept"} <kbd>A</kbd>
          </button>
          <button type="button" className="reject-action" onClick={onReject}>
            Reject <kbd>R</kbd>
          </button>
          <button type="button" onClick={onFlag}>
            Flag <kbd>F</kbd>
          </button>
          <button type="button" onClick={onDuplicate}>
            Duplicate <kbd>D</kbd>
          </button>
        </div>
      )}
      <dl>
        <div>
          <dt>{box.classType === "gap" ? "Origin" : "Assigned SKU"}</dt>
          <dd>
            {box.classType === "gap"
              ? box.confidence === null
                ? "Human drawn or corrected"
                : "Initial geometry candidate"
              : skuLabel(box)}
          </dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd>
            {box.state}, {overlayCue(box)} cue
          </dd>
        </div>
        <div>
          <dt>Class</dt>
          <dd>
            {lockGapClass ? (
              "Visible gap"
            ) : (
              <select
              className="annotation-class-select"
              aria-label="Annotation class"
              value={box.classType}
              onChange={(event) =>
                onClassChange(event.currentTarget.value as AnnotationClass)
              }
            >
              <option value="product">Product</option>
              <option value="gap">Visible gap</option>
              <option value="shelf_label">Shelf label</option>
              </select>
            )}
          </dd>
        </div>
        <div>
          <dt>Shelf row</dt>
          <dd>
            <input
              className="shelf-row-input"
              aria-label="Shelf row number"
              type="number"
              min="0"
              step="1"
              placeholder="Auto"
              value={box.shelfRow ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                if (value === "") {
                  onShelfRowChange(null);
                  return;
                }
                const row = Number(value);
                if (Number.isInteger(row) && row >= 0) {
                  onShelfRowChange(row);
                }
              }}
            />
          </dd>
        </div>
        <div>
          <dt>{box.classType === "gap" ? "Candidate score" : "Confidence"}</dt>
          <dd>
            {box.confidence === null ? "Not applicable" : box.confidence.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt>Bounding box</dt>
          <dd>
            {Math.round(box.x)}, {Math.round(box.y)}, {Math.round(box.width)},{" "}
            {Math.round(box.height)}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function AutosavePanel({
  mode,
  onModeChange,
  status,
  message,
  onSave,
  conflict,
  onUseServer,
  onKeepLocal,
}: {
  mode: AutosaveMode;
  onModeChange: (mode: AutosaveMode) => void;
  status: SaveStatus;
  message: string;
  onSave: () => void;
  conflict: AnnotationConflict | null;
  onUseServer: () => void;
  onKeepLocal: () => void;
}) {
  return (
    <section className="autosave-panel" aria-label="Save status">
      <div className="autosave-heading">
        <span className="eyebrow">Save status</span>
        <span className="save-state" data-status={status} role="status">
          {status}
        </span>
      </div>
      <p>{message}</p>
      <div className="autosave-controls">
        <label>
          Autosave
          <select
            value={mode}
            onChange={(event) =>
              onModeChange(event.target.value as AutosaveMode)
            }
          >
            <option value="quick">After 300 ms</option>
            <option value="safe">After 1 second</option>
            <option value="manual">Manual</option>
          </select>
        </label>
        <button type="button" onClick={onSave} disabled={status === "saved"}>
          Save now
        </button>
      </div>
      {conflict && (
        <div className="conflict-panel" role="alert">
          <strong>Both versions are preserved</strong>
          <span>
            Server revision {conflict.server.revision}; your local edit is still
            on the canvas.
          </span>
          <div>
            <button type="button" onClick={onKeepLocal}>
              Keep my edit
            </button>
            <button type="button" onClick={onUseServer}>
              Use server
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function BenchmarkOutput({
  benchmark,
  error,
}: {
  benchmark: CanvasBenchmark | null;
  error: string | null;
}) {
  if (error) {
    return (
      <p className="benchmark-output benchmark-error" role="alert">
        {error}
      </p>
    );
  }
  if (!benchmark) {
    return (
      <p className="benchmark-output">
        Measure p50, p95, and maximum frame and selection timings with the loaded
        fixture.
      </p>
    );
  }
  return (
    <div className="benchmark-output" data-testid="benchmark-results">
      {!benchmark.host.valid && (
        <p className="benchmark-invalid" role="alert" data-testid="benchmark-invalid">
          <strong>Run not valid on this machine.</strong> The timings below describe the
          host, not the canvas: {benchmark.host.reasons.join("; ")}. Close other
          applications and measure again.
        </p>
      )}
      <dl>
      <Metric
        label="Browser frame baseline"
        value={benchmark.browserFrameBaseline}
      />
      <Metric label="Canvas frame interval" value={benchmark.frameInterval} />
      <Metric label="Selection update" value={benchmark.selectionUpdate} />
      <Metric label="Input latency" value={benchmark.inputLatency} />
      <div>
        <dt>Measured at</dt>
        <dd data-testid="benchmark-timestamp">
          {new Date(benchmark.measuredAt).toLocaleTimeString()}
        </dd>
      </div>
      <div>
        <dt>Host</dt>
        <dd data-testid="benchmark-host">{benchmark.host.valid ? "Valid" : "Not valid"}</dd>
      </div>
      </dl>
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: { p50Ms: number; p95Ms: number; maxMs: number };
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {value.p50Ms} / {value.p95Ms} / {value.maxMs} ms
      </dd>
    </div>
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

function skuLabel(box: AnnotationBox): string {
  if (box.classType === "gap") {
    return "Visible gap";
  }
  if (box.classType === "shelf_label") {
    return "Shelf label";
  }
  return box.skuId === null ? "Unassigned" : box.sku;
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

function isControlTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("button, input, select, textarea") !== null;
}

function scenePoint(
  event: React.PointerEvent<HTMLDivElement>,
  transform: SceneTransform,
) {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left - transform.x) / transform.scale,
    y: (event.clientY - bounds.top - transform.y) / transform.scale,
  };
}

function imageIndexAtPoint(
  fixture: CanvasFixture,
  point: { x: number; y: number },
): number {
  return fixture.images.findIndex(
    (image) =>
      point.x >= image.x &&
      point.x <= image.x + image.width &&
      point.y >= image.y &&
      point.y <= image.y + image.height,
  );
}

function nearestShelfRow(annotations: AnnotationBox[], y: number): number {
  const rowCandidates = annotations.filter((box) => box.shelfRow !== null);
  if (rowCandidates.length === 0) {
    return 0;
  }
  return rowCandidates.reduce((closest, box) =>
    Math.abs(box.y + box.height / 2 - y) <
    Math.abs(closest.y + closest.height / 2 - y)
      ? box
      : closest,
  ).shelfRow!;
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
