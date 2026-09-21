import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthGate } from "./auth/AuthGate";
import { ProjectRouter } from "./projects/ProjectRouter";
import "./styles.css";

const root = document.querySelector("#root");

if (!root) {
  throw new Error("Application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <AuthGate>
      {(session, signOut) => (
        <ProjectRouter currentUser={session} onLogout={signOut} />
      )}
    </AuthGate>
  </StrictMode>,
);
