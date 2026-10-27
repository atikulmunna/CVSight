import { useState } from "react";

type SmartFindPanelProps = {
  // The selected products that serve as example boxes.
  exampleCount: number;
  pending: boolean;
  message: string | null;
  onFind: (text: string) => void;
};

// Finds every product like the selected ones, or matching a phrase, and adds the ones
// not labeled yet as proposals to review.
export function SmartFindPanel({ exampleCount, pending, message, onFind }: SmartFindPanelProps) {
  const [text, setText] = useState("");
  const canFind = !pending && (text.trim() !== "" || exampleCount > 0);

  return (
    <section className="smart-find-panel" aria-label="Find with SAM">
      <span className="eyebrow">Find with SAM</span>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (canFind) {
            onFind(text);
          }
        }}
      >
        <input
          type="text"
          aria-label="Find products named"
          placeholder="Products named, e.g. bottle"
          maxLength={200}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="submit" disabled={!canFind}>
          {pending ? "Finding" : "Find"}
        </button>
      </form>
      <p>
        {exampleCount > 0
          ? `Finds products like the ${exampleCount} selected.`
          : "Select products to find more like them, or name them."}
      </p>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
