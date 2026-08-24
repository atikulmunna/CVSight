export type UserRole = "owner" | "annotator" | "reviewer";

export type AuthSession = {
  username: string;
  role: UserRole;
};

export async function loadSession(): Promise<AuthSession | null> {
  const response = await fetch("/api/auth/session", {
    headers: { Accept: "application/json" },
  });
  if (response.status === 401) {
    return null;
  }
  if (!response.ok) {
    throw new Error("Authentication service is unavailable.");
  }
  return parseSession(await response.json());
}

export async function login(username: string, password: string): Promise<AuthSession> {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ username, password }),
  });
  if (response.status === 401) {
    throw new Error("Username or password is incorrect.");
  }
  if (!response.ok) {
    throw new Error("Sign in is unavailable.");
  }
  return parseSession(await response.json());
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  if (!response.ok && response.status !== 401) {
    throw new Error("Sign out failed.");
  }
}

function parseSession(value: unknown): AuthSession {
  if (!value || typeof value !== "object") {
    throw new Error("Authentication response is invalid.");
  }
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.username !== "string" ||
    !["owner", "annotator", "reviewer"].includes(String(candidate.role))
  ) {
    throw new Error("Authentication response is invalid.");
  }
  return {
    username: candidate.username,
    role: candidate.role as UserRole,
  };
}
