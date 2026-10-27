import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AuthGate } from "./auth/AuthGate";
import { ProjectRouter } from "./projects/ProjectRouter";
// Archivo's width axis carries the type: condensed for dense shelf data, normal for the
// interface, expanded for page titles.
import "@fontsource-variable/archivo/wdth.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
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
