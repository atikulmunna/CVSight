import type { AuthSession } from "../auth/api";

type ProjectsHeaderProps = {
  currentUser: AuthSession;
  section: string;
  onLogout: () => Promise<void>;
};

export function ProjectsHeader({
  currentUser,
  section,
  onLogout,
}: ProjectsHeaderProps) {
  return (
    <header className="projects-topbar">
      <a className="projects-brand" href="/projects" aria-label="CVSight projects">
        <img src="/cvsight-mark.svg" alt="" />
        <span>CV<strong>Sight</strong></span>
      </a>
      <span className="projects-section-label">{section}</span>
      <div className="projects-session">
        <span className="session-identity">
          <strong>{currentUser.username}</strong>
          <span className={`role-chip ${currentUser.role}`}>{currentUser.role}</span>
        </span>
        <button type="button" onClick={() => void onLogout()}>Sign out</button>
      </div>
    </header>
  );
}
