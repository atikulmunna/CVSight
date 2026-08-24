import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { AuthGate } from "./auth/AuthGate";
import "./styles.css";

const root = document.querySelector("#root");

if (!root) {
  throw new Error("Application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <AuthGate>
      {(session, signOut) => (
        <App currentUser={session} onLogout={signOut} />
      )}
    </AuthGate>
  </StrictMode>,
);
