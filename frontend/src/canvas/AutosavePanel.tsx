import type {
  AnnotationConflict,
  AutosaveMode,
  SaveStatus,
} from "./useAnnotationAutosave";

export function AutosavePanel({
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
