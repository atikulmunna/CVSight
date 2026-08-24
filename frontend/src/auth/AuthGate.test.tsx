import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AuthGate", () => {
  it("signs in, renders the session, and signs out", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401 })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ username: "review.one", role: "reviewer" }),
      })
      .mockResolvedValueOnce({ ok: true, status: 204 });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthGate>
        {(session, signOut) => (
          <div>
            <span>{session.username}</span>
            <button type="button" onClick={() => void signOut()}>Leave</button>
          </div>
        )}
      </AuthGate>,
    );

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "review.one" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "private-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("review.one")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
  });

  it("shows a generic authentication outage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("database password leaked")));

    render(<AuthGate>{() => <span>private app</span>}</AuthGate>);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Authentication service is unavailable.",
    );
    expect(screen.queryByText("database password leaked")).not.toBeInTheDocument();
  });
});
