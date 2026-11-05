import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_DISPLAY } from "./display";
import { DisplayPanel } from "./DisplayPanel";

const FOLDED_KEY = "shelfsight:canvas-display-folded";

function renderPanel(onChange = vi.fn()) {
  return render(
    <DisplayPanel
      settings={{ ...DEFAULT_DISPLAY, brightness: 150 }}
      hideAll={false}
      onChange={onChange}
      onHideAllChange={vi.fn()}
    />,
  );
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("DisplayPanel folding", () => {
  it("starts open and folds away its controls", () => {
    renderPanel();
    const toggle = screen.getByRole("button", { name: "Display" });

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText(/Brightness/)).toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText(/Brightness/)).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Boxes shown" })).not.toBeInTheDocument();
  });

  it("stays folded on the next photo, which mounts a new panel", () => {
    const first = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Display" }));
    first.unmount();

    renderPanel();

    expect(screen.getByRole("button", { name: "Display" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByLabelText(/Brightness/)).not.toBeInTheDocument();
  });

  it("opens again and shows the same settings", () => {
    localStorage.setItem(FOLDED_KEY, "true");
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Display" }));

    expect(screen.getByLabelText(/Brightness/)).toHaveValue("150");
    expect(localStorage.getItem(FOLDED_KEY)).toBe("false");
  });

  it("keeps Reset available while folded", () => {
    localStorage.setItem(FOLDED_KEY, "true");
    const onChange = vi.fn();
    renderPanel(onChange);

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));

    expect(onChange).toHaveBeenCalledWith(DEFAULT_DISPLAY);
  });

  it("still folds when the browser refuses storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage is blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage is blocked");
    });
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Display" }));

    expect(screen.queryByLabelText(/Brightness/)).not.toBeInTheDocument();
  });
});
