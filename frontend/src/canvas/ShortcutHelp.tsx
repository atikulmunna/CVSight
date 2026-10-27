import { useEffect, useRef } from "react";

type Context = {
  mode: "verify" | "assign";
  isGapReview: boolean;
  outlinesProducts: boolean;
};

type Shortcut = {
  keys: string;
  action: string;
  when?: (context: Context) => boolean;
};

const verifying = ({ mode }: Context) => mode === "verify";
const assigning = ({ mode }: Context) => mode === "assign";
const drawingProducts = (context: Context) => verifying(context) && !context.isGapReview;
const smartBoxing = (context: Context) => drawingProducts(context) && !context.outlinesProducts;
const smartOutlining = (context: Context) => drawingProducts(context) && context.outlinesProducts;

const GROUPS: Array<{ title: string; shortcuts: Shortcut[] }> = [
  {
    title: "Review",
    shortcuts: [
      { keys: "A", action: "Accept", when: verifying },
      { keys: "R, Delete", action: "Reject", when: verifying },
      { keys: "F", action: "Flag", when: verifying },
      { keys: "D", action: "Duplicate", when: verifying },
      { keys: "/", action: "Change the SKU", when: verifying },
      { keys: "1 to 9", action: "Assign a candidate SKU", when: assigning },
      { keys: "S", action: "Assign the previous SKU", when: assigning },
      { keys: "U", action: "Assign Unknown / Other", when: assigning },
      { keys: "/", action: "Search the catalog", when: assigning },
    ],
  },
  {
    title: "Select",
    shortcuts: [
      { keys: "J, K or arrows", action: "Next or previous box" },
      { keys: "Shift + click", action: "Add or remove a box" },
      { keys: "Drag on empty canvas", action: "Select the boxes inside" },
      { keys: "Ctrl + A", action: "Select all" },
      { keys: "Esc", action: "Clear the selection" },
    ],
  },
  {
    title: "Edit",
    shortcuts: [
      { keys: "Drag a box or handle", action: "Move or resize it", when: verifying },
      { keys: "Alt + arrows", action: "Nudge by one pixel", when: verifying },
      { keys: "Alt + Shift + arrows", action: "Resize by one pixel", when: verifying },
      { keys: "Ctrl + C", action: "Copy the selected boxes" },
      { keys: "Ctrl + V", action: "Paste, also on another photo", when: verifying },
      { keys: "Ctrl + Z", action: "Undo" },
      { keys: "Ctrl + Shift + Z, Ctrl + Y", action: "Redo" },
    ],
  },
  {
    title: "Tools",
    shortcuts: [
      { keys: "V", action: "Select" },
      { keys: "H", action: "Pan" },
      {
        keys: "B",
        action: "Draw a box, named with the previous SKU",
        when: drawingProducts,
      },
      {
        keys: "M",
        action: "Smart box: click a product and SAM boxes it",
        when: smartBoxing,
      },
      {
        keys: "M",
        action: "Smart polygon: click a product and SAM outlines it",
        when: smartOutlining,
      },
      {
        keys: "Click, Shift + click",
        action: "Add an area to the outline, or remove one",
        when: smartOutlining,
      },
      { keys: "Enter, Esc", action: "Finish the outline, or cancel it", when: smartOutlining },
      { keys: "G", action: "Draw a gap", when: verifying },
    ],
  },
  {
    title: "View",
    shortcuts: [
      { keys: "+, - or wheel", action: "Zoom" },
      { keys: "Space + drag", action: "Pan" },
      { keys: "O", action: "Hide or show all boxes" },
      { keys: "?", action: "Show this list" },
    ],
  },
];

export function ShortcutHelp({ context, onClose }: { context: Context; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  return (
    <div className="shortcut-help-backdrop" onPointerDown={onClose}>
      <div
        className="shortcut-help"
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        onPointerDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape" || event.key === "?") {
            event.preventDefault();
            onClose();
          } else if (event.key === "Tab") {
            // The close button is the only control, so focus stays on it.
            event.preventDefault();
          }
        }}
      >
        <div className="shortcut-help-heading">
          <strong>Keyboard shortcuts</strong>
          <button type="button" ref={closeRef} aria-label="Close shortcuts" onClick={onClose}>
            ×
          </button>
        </div>
        <p>Cmd works in place of Ctrl on macOS.</p>
        <div className="shortcut-help-groups">
          {GROUPS.map((group) => {
            const shortcuts = group.shortcuts.filter(
              (shortcut) => shortcut.when?.(context) ?? true,
            );
            return (
              <section key={group.title} aria-label={group.title}>
                <h3>{group.title}</h3>
                <dl>
                  {shortcuts.map((shortcut) => (
                    <div key={`${shortcut.keys} ${shortcut.action}`}>
                      <dt>
                        <kbd>{shortcut.keys}</kbd>
                      </dt>
                      <dd>{shortcut.action}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}
