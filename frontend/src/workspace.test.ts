import { describe, expect, it } from "vitest";

import { workspaceAllowed, workspacesForRole } from "./workspace";

describe("workspace access", () => {
  it("gives owners access to every workspace", () => {
    expect(workspacesForRole("owner")).toEqual([
      "verify",
      "assign",
      "propagate",
      "review",
      "catalog",
      "analytics",
    ]);
  });

  it("keeps annotators out of review and owner workspaces", () => {
    expect(workspaceAllowed("annotator", "propagate")).toBe(true);
    expect(workspaceAllowed("annotator", "review")).toBe(false);
    expect(workspaceAllowed("annotator", "catalog")).toBe(false);
  });

  it("allows reviewers to annotate and review without owner tools", () => {
    expect(workspaceAllowed("reviewer", "verify")).toBe(true);
    expect(workspaceAllowed("reviewer", "review")).toBe(true);
    expect(workspaceAllowed("reviewer", "analytics")).toBe(false);
  });
});
