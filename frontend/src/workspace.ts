import type { UserRole } from "./auth/api";

export type Workspace =
  | "verify"
  | "assign"
  | "propagate"
  | "review"
  | "catalog"
  | "analytics";

const ROLE_WORKSPACES: Record<UserRole, readonly Workspace[]> = {
  owner: ["verify", "assign", "propagate", "review", "catalog", "analytics"],
  annotator: ["verify", "assign", "propagate"],
  reviewer: ["verify", "assign", "review"],
};

export function workspacesForRole(role: UserRole): readonly Workspace[] {
  return ROLE_WORKSPACES[role];
}

export function workspaceAllowed(role: UserRole, workspace: Workspace): boolean {
  return workspacesForRole(role).includes(workspace);
}
