import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DatasetExportForm } from "./DatasetExportForm";

const VERSION_ID = "44444444-4444-4444-8444-444444444444";
const BASE = `/api/dataset-versions/${VERSION_ID}/exports`;

describe("training dataset export", () => {
  it("offers a YOLO product export with a 70/20/10 split by default", () => {
    render(<DatasetExportForm versionId={VERSION_ID} generated={[]} />);

    expect(screen.getByRole("link", { name: "Download YOLO ZIP" })).toHaveAttribute(
      "href",
      `${BASE}/yolo?classes=product&split=70%2C20%2C10`,
    );
  });

  it("builds the download from the chosen format, classes, and split", () => {
    render(<DatasetExportForm versionId={VERSION_ID} generated={[]} />);

    fireEvent.change(screen.getByLabelText("Format"), { target: { value: "createml" } });
    fireEvent.change(screen.getByLabelText("Classes"), { target: { value: "sku" } });
    fireEvent.change(screen.getByLabelText("Valid %"), { target: { value: "15" } });
    fireEvent.change(screen.getByLabelText("Test %"), { target: { value: "15" } });

    expect(screen.getByRole("link", { name: "Download CreateML JSON ZIP" })).toHaveAttribute(
      "href",
      `${BASE}/createml?classes=sku&split=70%2C15%2C15`,
    );
  });

  it("replaces the download with the problem while the split is invalid", () => {
    render(<DatasetExportForm versionId={VERSION_ID} generated={[]} />);

    fireEvent.change(screen.getByLabelText("Train %"), { target: { value: "80" } });

    expect(screen.getByRole("alert")).toHaveTextContent("The split adds up to 110%");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("lists training formats that were generated before", () => {
    render(<DatasetExportForm versionId={VERSION_ID} generated={["detection", "csv", "yolo"]} />);

    expect(screen.getByText(/Previously generated: YOLO, CSV\./)).toBeInTheDocument();
  });
});
