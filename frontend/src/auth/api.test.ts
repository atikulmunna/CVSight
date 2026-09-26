import { afterEach, describe, expect, it, vi } from "vitest";

import { loadSession, login, logout } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("authentication API", () => {
  it("treats an unauthorized session check as signed out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));

    await expect(loadSession()).resolves.toBeNull();
  });

  it("sends credentials only in the login request body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ username: "labeler", role: "annotator" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("labeler", "private-password")).resolves.toEqual({
      username: "labeler",
      role: "annotator",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ username: "labeler", password: "private-password" }),
      }),
    );
  });

  it("tells a rate-limited person to wait rather than reporting an outage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 429 }));

    await expect(login("labeler", "guess")).rejects.toThrow("Too many sign-in attempts");
  });

  it("rejects invalid session data and tolerates an expired logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ username: "user", role: "admin" }),
        })
        .mockResolvedValueOnce({ ok: false, status: 401 }),
    );

    await expect(loadSession()).rejects.toThrow("response is invalid");
    await expect(logout()).resolves.toBeUndefined();
  });
});
