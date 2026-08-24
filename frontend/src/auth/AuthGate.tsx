import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { loadSession, login, logout, type AuthSession } from "./api";

type AuthGateProps = {
  children: (session: AuthSession, signOut: () => Promise<void>) => ReactNode;
};

export function AuthGate({ children }: AuthGateProps) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    loadSession()
      .then((loaded) => {
        if (active) {
          setSession(loaded);
          setChecking(false);
        }
      })
      .catch(() => {
        if (active) {
          setError("Authentication service is unavailable.");
          setChecking(false);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  async function signIn(username: string, password: string) {
    setError(null);
    try {
      setSession(await login(username, password));
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Sign in failed.");
    }
  }

  async function signOut() {
    await logout();
    setSession(null);
  }

  if (checking) {
    return <div className="auth-loading">Checking session</div>;
  }
  if (session) {
    return children(session, signOut);
  }
  return <LoginScreen error={error} onSubmit={signIn} />;
}

type LoginScreenProps = {
  error: string | null;
  onSubmit: (username: string, password: string) => Promise<void>;
};

function LoginScreen({ error, onSubmit }: LoginScreenProps) {
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    await onSubmit(String(fields.get("username") ?? ""), String(fields.get("password") ?? ""));
    setBusy(false);
  }

  return (
    <main className="auth-shell">
      <form className="auth-card" onSubmit={(event) => void submit(event)}>
        <div className="auth-brand">
          <img src="/cvsight-mark.png" alt="" />
          <span>CV<strong>Sight</strong></span>
        </div>
        <div>
          <p className="eyebrow">SECURE WORKSPACE</p>
          <h1>Sign in</h1>
          <p>Use the local account configured by this CVSight server.</p>
        </div>
        <label>
          Username
          <input name="username" autoComplete="username" required maxLength={64} autoFocus />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={1024}
          />
        </label>
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button type="submit" disabled={busy}>{busy ? "Signing in" : "Sign in"}</button>
      </form>
    </main>
  );
}
