import { useCallback, useEffect, useRef, useState } from "react";

import {
  AnnotationSaveError,
  fetchAnnotation,
  saveAnnotation,
} from "./annotationApi";
import type { EditIntent } from "./editing";
import {
  parseCanvasFixture,
  type AnnotationBox,
  type CanvasFixture,
} from "./model";

export type SaveStatus = "saved" | "saving" | "offline" | "conflict";
export type AutosaveMode = "quick" | "safe" | "manual";

type PendingEdit = {
  id: string;
  intent: EditIntent;
  box: AnnotationBox;
};

export type AnnotationConflict = {
  local: AnnotationBox;
  server: AnnotationBox;
  intent: EditIntent;
};

const SAVE_DELAYS: Record<AutosaveMode, number | null> = {
  quick: 300,
  safe: 1_000,
  manual: null,
};

export function useAnnotationAutosave(fixture: CanvasFixture) {
  const [restored] = useState(() => restoreWorkspace(fixture));
  const [annotations, setAnnotations] = useState(restored.annotations);
  const [mode, setMode] = useState<AutosaveMode>("safe");
  const [status, setStatus] = useState<SaveStatus>(
    restored.pending.length > 0 ? "saving" : "saved",
  );
  const [message, setMessage] = useState(
    restored.wasRestored ? "Recovered local work" : "All changes saved",
  );
  const [conflict, setConflict] = useState<AnnotationConflict | null>(null);
  const [queueVersion, setQueueVersion] = useState(0);
  const annotationsRef = useRef(annotations);
  const pendingRef = useRef(restored.pending);
  const savingRef = useRef(false);
  const storageKey = workspaceStorageKey(fixture);

  const store = useCallback(
    (nextAnnotations: AnnotationBox[], pending: PendingEdit[]) => {
      try {
        localStorage.setItem(
          storageKey,
          JSON.stringify({
            version: 1,
            annotations: nextAnnotations.map(storedBox),
            pending: pending.map(({ id, intent }) => ({ id, intent })),
          }),
        );
        return true;
      } catch {
        setStatus("offline");
        setMessage("Local draft storage is unavailable");
        return false;
      }
    },
    [storageKey],
  );

  const replaceAnnotations = useCallback(
    (next: AnnotationBox[]) => {
      annotationsRef.current = next;
      setAnnotations(next);
      store(next, pendingRef.current);
    },
    [store],
  );

  const queueEdit = useCallback(
    (box: AnnotationBox, intent: EditIntent) => {
      const queue = pendingRef.current;
      const last = queue.at(-1);
      if (intent === "update" && last?.id === box.id && last.intent === "update") {
        queue[queue.length - 1] = { id: box.id, intent, box };
      } else {
        queue.push({ id: box.id, intent, box });
      }
      store(annotationsRef.current, queue);
      setStatus("saving");
      setMessage(mode === "manual" ? "Unsaved changes" : "Saving changes");
      setQueueVersion((value) => value + 1);
    },
    [mode, store],
  );

  const edit = useCallback(
    (
      id: string,
      intent: EditIntent,
      update: (box: AnnotationBox) => AnnotationBox,
    ) => {
      const current = annotationsRef.current;
      const index = current.findIndex((box) => box.id === id);
      if (index < 0) {
        return;
      }
      const nextBox = update(current[index]!);
      if (nextBox === current[index]) {
        return;
      }
      const next = [...current];
      next[index] = nextBox;
      replaceAnnotations(next);
      queueEdit(nextBox, intent);
    },
    [queueEdit, replaceAnnotations],
  );

  const add = useCallback(
    (box: AnnotationBox) => {
      const next = [...annotationsRef.current, box];
      replaceAnnotations(next);
      queueEdit(box, "create");
    },
    [queueEdit, replaceAnnotations],
  );

  const flush = useCallback(async () => {
    if (savingRef.current || conflict || pendingRef.current.length === 0) {
      return;
    }
    savingRef.current = true;
    setStatus("saving");
    setMessage("Saving changes");
    try {
      while (pendingRef.current.length > 0) {
        const pending = pendingRef.current[0]!;
        const saved = await saveAnnotation(pending.box, pending.intent);
        pendingRef.current.shift();
        pendingRef.current = pendingRef.current.map((item) =>
          item.id === pending.id
            ? {
                ...item,
                box: {
                  ...item.box,
                  serverId: saved.serverId,
                  imageId: saved.imageId,
                  revision: saved.revision,
                },
              }
            : item,
        );
        const hasNewerEdit = pendingRef.current.some(
          (item) => item.id === pending.id,
        );
        const next = annotationsRef.current.map((box) => {
          if (box.id !== pending.id) {
            return box;
          }
          return hasNewerEdit
            ? {
                ...box,
                serverId: saved.serverId,
                imageId: saved.imageId,
                revision: saved.revision,
              }
            : saved;
        });
        annotationsRef.current = next;
        setAnnotations(next);
        store(next, pendingRef.current);
      }
      setStatus("saved");
      setMessage("All changes saved");
    } catch (error) {
      const pending = pendingRef.current[0];
      if (
        pending &&
        error instanceof AnnotationSaveError &&
        error.status === 409 &&
        error.code === "stale_revision" &&
        pending.box.serverId
      ) {
        try {
          const server = await fetchAnnotation(pending.box);
          const local =
            annotationsRef.current.find((box) => box.id === pending.id) ??
            pending.box;
          setConflict({ local, server, intent: pending.intent });
          setStatus("conflict");
          setMessage("Server and local changes conflict");
          return;
        } catch {
          setStatus("offline");
          setMessage("Offline. Local changes are safe");
          return;
        }
      }
      setStatus("offline");
      setMessage("Offline. Local changes are safe");
    } finally {
      savingRef.current = false;
    }
  }, [conflict, store]);

  const useServerVersion = useCallback(() => {
    if (!conflict) {
      return;
    }
    pendingRef.current = pendingRef.current.filter(
      (item) => item.id !== conflict.local.id,
    );
    const next = annotationsRef.current.map((box) =>
      box.id === conflict.local.id ? conflict.server : box,
    );
    annotationsRef.current = next;
    setAnnotations(next);
    store(next, pendingRef.current);
    setConflict(null);
    setStatus(pendingRef.current.length > 0 ? "saving" : "saved");
    setMessage(
      pendingRef.current.length > 0 ? "Saving changes" : "Using server version",
    );
    setQueueVersion((value) => value + 1);
  }, [conflict, store]);

  const keepLocalVersion = useCallback(() => {
    if (!conflict) {
      return;
    }
    const rebased = {
      ...conflict.local,
      serverId: conflict.server.serverId,
      imageId: conflict.server.imageId,
      revision: conflict.server.revision,
    };
    pendingRef.current = pendingRef.current.filter(
      (item) => item.id !== conflict.local.id,
    );
    pendingRef.current.unshift({
      id: rebased.id,
      intent: conflict.intent,
      box: rebased,
    });
    const next = annotationsRef.current.map((box) =>
      box.id === rebased.id ? rebased : box,
    );
    annotationsRef.current = next;
    setAnnotations(next);
    store(next, pendingRef.current);
    setConflict(null);
    setStatus("saving");
    setMessage("Retrying local change");
    setQueueVersion((value) => value + 1);
  }, [conflict, store]);

  useEffect(() => {
    const delay = SAVE_DELAYS[mode];
    if (delay === null || pendingRef.current.length === 0 || conflict) {
      return;
    }
    const timer = window.setTimeout(() => void flush(), delay);
    return () => window.clearTimeout(timer);
  }, [conflict, flush, mode, queueVersion]);

  useEffect(() => {
    const retry = () => {
      if (pendingRef.current.length > 0 && !conflict) {
        void flush();
      }
    };
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, [conflict, flush]);

  return {
    annotations,
    edit,
    add,
    mode,
    setMode,
    status,
    message,
    conflict,
    flush,
    useServerVersion,
    keepLocalVersion,
  };
}

function workspaceStorageKey(fixture: CanvasFixture): string {
  const imageIds = fixture.images.map((image) => image.id).join(",");
  return `shelfsight:annotation-draft:${imageIds}`;
}

function restoreWorkspace(fixture: CanvasFixture): {
  annotations: AnnotationBox[];
  pending: PendingEdit[];
  wasRestored: boolean;
} {
  try {
    const raw = localStorage.getItem(workspaceStorageKey(fixture));
    if (!raw) {
      return { annotations: fixture.annotations, pending: [], wasRestored: false };
    }
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("invalid draft");
    }
    const stored = value as Record<string, unknown>;
    if (stored.version !== 1 || !Array.isArray(stored.annotations)) {
      throw new Error("invalid draft");
    }
    const draft = parseCanvasFixture({
      name: fixture.name,
      image: { width: fixture.width, height: fixture.height },
      images: fixture.images,
      boxes: stored.annotations,
    }).annotations;
    const pending: PendingEdit[] = [];
    if (Array.isArray(stored.pending)) {
      for (const item of stored.pending) {
        if (!item || typeof item !== "object" || Array.isArray(item)) {
          continue;
        }
        const candidate = item as Record<string, unknown>;
        if (
          typeof candidate.id === "string" &&
          isEditIntent(candidate.intent) &&
          draft.some((box) => box.id === candidate.id)
        ) {
          pending.push({
            id: candidate.id,
            intent: candidate.intent,
            box: draft.find((box) => box.id === candidate.id)!,
          });
        }
      }
    }
    const merged = mergeDraft(
      fixture.annotations,
      draft,
      new Set(pending.map((edit) => edit.id)),
    );
    return { annotations: merged.boxes, pending, wasRestored: merged.usedDraft };
  } catch {
    return { annotations: fixture.annotations, pending: [], wasRestored: false };
  }
}

// The freshly loaded server copy wins unless the draft holds something the server does
// not have yet: an unsaved edit, a box that exists only locally, or a newer revision.
// Restoring the whole draft instead would hide server changes, such as SKU names.
function mergeDraft(
  serverBoxes: AnnotationBox[],
  draftBoxes: AnnotationBox[],
  pendingIds: Set<string>,
): { boxes: AnnotationBox[]; usedDraft: boolean } {
  const key = (box: AnnotationBox) => box.serverId ?? box.id;
  const draftByKey = new Map(draftBoxes.map((box) => [key(box), box]));
  let usedDraft = false;
  const boxes = serverBoxes.map((server) => {
    const draft = draftByKey.get(key(server));
    const keepDraft =
      draft !== undefined &&
      (pendingIds.has(draft.id) ||
        draft.serverId === null ||
        (draft.revision ?? 0) > (server.revision ?? 0));
    if (keepDraft) {
      usedDraft = true;
      return draft;
    }
    return server;
  });
  const serverKeys = new Set(serverBoxes.map(key));
  for (const draft of draftBoxes) {
    if (
      !serverKeys.has(key(draft)) &&
      (pendingIds.has(draft.id) || draft.serverId === null)
    ) {
      boxes.push(draft);
      usedDraft = true;
    }
  }
  return { boxes, usedDraft };
}

function storedBox(box: AnnotationBox): Record<string, unknown> {
  return {
    id: box.id,
    server_id: box.serverId,
    image_id: box.imageId,
    revision: box.revision,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
    kind: box.classType,
    state: box.state,
    lifecycle_state: box.lifecycleState,
    review_state: box.reviewState,
    sku: box.sku,
    sku_id: box.skuId,
    confidence: box.confidence,
    occluded: box.occluded,
    truncated: box.truncated,
    shelf_row: box.shelfRow,
    image_index: box.imageIndex,
  };
}

function isEditIntent(value: unknown): value is EditIntent {
  return (
    value === "accept" ||
    value === "reject" ||
    value === "update" ||
    value === "create" ||
    value === "assign"
  );
}
