import type { ReactNode } from "react";

export type CanvasTool = "select" | "pan" | "product" | "gap";

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
    tool: "gap",
    label: "Draw gap",
    shortcut: "G",
    icon: <rect x="4.5" y="4.5" width="15" height="15" rx="1" strokeDasharray="3 2.4" />,
  },
];

type CanvasToolsProps = {
  tool: CanvasTool;
  available: readonly CanvasTool[];
  onChange: (tool: CanvasTool) => void;
};

export function CanvasTools({ tool, available, onChange }: CanvasToolsProps) {
  return (
    <div className="canvas-tools" role="toolbar" aria-label="Canvas tools" aria-orientation="vertical">
      {TOOLS.filter((spec) => available.includes(spec.tool)).map((spec) => (
        <button
          type="button"
          className={`canvas-tool${tool === spec.tool ? " is-active" : ""}`}
          aria-label={spec.label}
          aria-pressed={tool === spec.tool}
          title={`${spec.label} (${spec.shortcut})`}
          onClick={() => onChange(spec.tool)}
          key={spec.tool}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            {spec.icon}
          </svg>
          <kbd>{spec.shortcut}</kbd>
        </button>
      ))}
    </div>
  );
}
