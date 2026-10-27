import type { ReactNode } from "react";

export type CanvasTool = "select" | "pan" | "product" | "smart" | "gap";

type ToolSpec = {
  tool: CanvasTool;
  label: string;
  shortcut: string;
  icon: ReactNode;
};

const TOOLS: ToolSpec[] = [
  {
    tool: "select",
    label: "Select",
    shortcut: "V",
    icon: <path d="M5 3.5v13.2l3.6-3.4 2.4 5.6 2.3-1-2.4-5.5H16z" />,
  },
  {
    tool: "pan",
    label: "Pan",
    shortcut: "H",
    icon: (
      <path d="M8.5 11.5V5.2a1.4 1.4 0 0 1 2.8 0v5.3m0-.5V4a1.4 1.4 0 0 1 2.8 0v6.5m0-.3V5.6a1.4 1.4 0 0 1 2.8 0v7.6a6.3 6.3 0 0 1-6.3 6.3h-.8a6 6 0 0 1-4.9-2.6l-2.6-3.9a1.4 1.4 0 0 1 2.2-1.7l2 2.1" />
    ),
  },
  {
    tool: "product",
    label: "Draw box",
    shortcut: "B",
    icon: (
      <>
        <rect x="5" y="5" width="14" height="14" rx="1" />
        <path d="M3.5 3.5h3v3h-3zM17.5 3.5h3v3h-3zM3.5 17.5h3v3h-3zM17.5 17.5h3v3h-3z" />
      </>
    ),
  },
  {
    tool: "smart",
    label: "Smart box",
    shortcut: "M",
    icon: <path d="M4.5 19.5l10-10M16 3.5v3M14.5 5h3M19.5 8.5v2M18.5 9.5h2M10 4v2M9 5h2" />,
  },
  {
    tool: "gap",
    label: "Draw gap",
    shortcut: "G",
    icon: <rect x="4.5" y="4.5" width="15" height="15" rx="1" strokeDasharray="3 2.4" />,
  },
];

type HistoryControls = {
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
};

type CanvasToolsProps = {
  tool: CanvasTool;
  available: readonly CanvasTool[];
  // Smart box in detection projects, Smart polygon where products are outlined.
  smartLabel: string;
  onChange: (tool: CanvasTool) => void;
  history: HistoryControls;
};

export function CanvasTools({
  tool,
  available,
  smartLabel,
  onChange,
  history,
}: CanvasToolsProps) {
  return (
    <div className="canvas-tools" role="toolbar" aria-label="Canvas tools" aria-orientation="vertical">
      {TOOLS.filter((spec) => available.includes(spec.tool)).map((spec) => {
        const label = spec.tool === "smart" ? smartLabel : spec.label;
        return (
          <button
            type="button"
            className={`canvas-tool${tool === spec.tool ? " is-active" : ""}`}
            aria-label={label}
            aria-pressed={tool === spec.tool}
            title={`${label} (${spec.shortcut})`}
            onClick={() => onChange(spec.tool)}
            key={spec.tool}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {spec.icon}
            </svg>
            <kbd>{spec.shortcut}</kbd>
          </button>
        );
      })}
      <span className="canvas-tools-divider" aria-hidden="true" />
      <button
        type="button"
        className="canvas-tool"
        aria-label="Undo"
        title="Undo (Ctrl+Z)"
        disabled={!history.canUndo}
        onClick={history.onUndo}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
        </svg>
      </button>
      <button
        type="button"
        className="canvas-tool"
        aria-label="Redo"
        title="Redo (Ctrl+Shift+Z)"
        disabled={!history.canRedo}
        onClick={history.onRedo}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m15 14 5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />
        </svg>
      </button>
    </div>
  );
}
