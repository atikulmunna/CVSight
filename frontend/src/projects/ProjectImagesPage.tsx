import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";

import type { AuthSession } from "../auth/api";
import { isMissingProject, loadProject, type ProjectSummary } from "./api";
import {
  loadProjectImages,
  ProjectImageApiError,
  uploadProjectImage,
  type ProjectImage,
  type ProjectImageStatus,
} from "./imagesApi";
import { ProjectBand } from "./ProjectBand";
import { ProjectsHeader } from "./ProjectsHeader";

type ProjectImagesPageProps = {
  projectId: string;
  currentUser: AuthSession;
  onBack: () => void;
  onOpenImage: (project: ProjectSummary, image: ProjectImage) => void;
  onOpenOverview: () => void;
  onOpenReview: (project: ProjectSummary) => void;
  onOpenVersions: (project: ProjectSummary) => void;
  onOpenModels: (project: ProjectSummary) => void;
  onOpenAnalytics: (project: ProjectSummary) => void;
  onLogout: () => Promise<void>;
};

type PageStatus = "loading" | "ready" | "missing" | "error";
type ImageFilter = ProjectImageStatus | "all";

const IMAGE_FILTERS: Array<{ value: ImageFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "unlabeled", label: "Unannotated" },
  { value: "pre_labeled", label: "Pre-labeled" },
  { value: "in_progress", label: "In progress" },
  { value: "labeled", label: "Labeled" },
  { value: "reviewed", label: "Reviewed" },
];

export function ProjectImagesPage({
  projectId,
  currentUser,
  onBack,
  onOpenImage,
  onOpenOverview,
  onOpenReview,
  onOpenVersions,
  onOpenModels,
  onOpenAnalytics,
  onLogout,
}: ProjectImagesPageProps) {
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [projectStatus, setProjectStatus] = useState<PageStatus>("loading");
  const [images, setImages] = useState<ProjectImage[]>([]);
  const [total, setTotal] = useState(0);
  const [imageStatus, setImageStatus] = useState<PageStatus>("loading");
  const [filter, setFilter] = useState<ImageFilter>("all");
  const [refreshKey, setRefreshKey] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [showUpload, setShowUpload] = useState(false);

  useEffect(() => {
    let active = true;
    loadProject(projectId)
      .then((selected) => {
        if (!active) {
          return;
        }
        setProject(selected);
        setProjectStatus("ready");
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        setProject(null);
        setProjectStatus(isMissingProject(error) ? "missing" : "error");
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!project) {
      return;
    }
    let active = true;
    loadProjectImages(
      project.id,
      project.latestVersionId,
      filter === "all" ? null : filter,
    )
      .then((page) => {
        if (active) {
          setImages(page.images);
          setTotal(page.total);
          setImageStatus("ready");
        }
      })
      .catch(() => {
        if (active) {
          setImageStatus("error");
        }
      });
    return () => {
      active = false;
    };
  }, [filter, project, refreshKey]);

  function changeFilter(nextFilter: ImageFilter) {
    if (nextFilter === filter) {
      return;
    }
    setImages([]);
    setTotal(0);
    setImageStatus("loading");
    setFilter(nextFilter);
  }

  function refreshImages() {
    setImages([]);
    setTotal(0);
    setImageStatus("loading");
    setRefreshKey((value) => value + 1);
  }

  async function loadMore() {
    if (!project || loadingMore || images.length >= total) {
      return;
    }
    try {
      setLoadingMore(true);
      const page = await loadProjectImages(
        project.id,
        project.latestVersionId,
        filter === "all" ? null : filter,
        images.length,
      );
      setImages((current) => [...current, ...page.images]);
      setTotal(page.total);
    } catch {
      setImageStatus("error");
    } finally {
      setLoadingMore(false);
    }
  }

  if (projectStatus !== "ready" || !project) {
    return (
      <div className="projects-shell">
        <ProjectsHeader currentUser={currentUser} section="Project images" onLogout={onLogout} />
        <ImagesMessage
          title={
            projectStatus === "loading"
              ? "Loading project"
              : projectStatus === "missing"
                ? "Project not found"
                : "Project unavailable"
          }
          detail={
            projectStatus === "loading"
              ? "Reading the project workspace."
              : projectStatus === "missing"
                ? "This project does not exist or is no longer available."
                : "The project service could not be reached."
          }
          onBack={projectStatus === "loading" ? undefined : onBack}
        />
      </div>
    );
  }

  const canUpload = currentUser.role === "owner" && project.openVersionId !== null;

  return (
    <div className="projects-shell">
      <ProjectsHeader currentUser={currentUser} section="Project images" onLogout={onLogout} />
      <main className="project-images-main">
        <ProjectBand
          project={project}
          currentUser={currentUser}
          active="images"
          onBack={onBack}
          onOverview={onOpenOverview}
          onImages={() => undefined}
          onReview={() => onOpenReview(project)}
          onVersions={() => onOpenVersions(project)}
          onModels={() => onOpenModels(project)}
          onAnalytics={() => onOpenAnalytics(project)}
        />

        <section className="project-images-heading">
          <div>
            <h2>Images</h2>
            <p>Upload shelf photos, track labeling state, and open an image for annotation.</p>
          </div>
          {canUpload && (
            <button className="projects-primary-action" type="button" onClick={() => setShowUpload(true)}>
              <span aria-hidden="true">+</span> Upload images
            </button>
          )}
        </section>

        {showUpload && project.openVersionId && (
          <ImageUploadPanel
            projectId={project.id}
            versionId={project.openVersionId}
            onClose={() => setShowUpload(false)}
            onUploaded={refreshImages}
          />
        )}

        {filter === "all" && imageStatus === "ready" && !images.some((image) => image.status === "reviewed") && (
          <section className="first-image-guide" aria-label="First image guidance">
            <span>{images.length === 0 ? "Step 2 of 3" : "Step 3 of 3"}</span>
            <div>
              <strong>{images.length === 0 ? "Upload your first shelf image" : "Open and review your first image"}</strong>
              <p>
                {images.length === 0
                  ? "Choose Upload images, select JPEG or PNG shelf photos, and wait for each file to show Uploaded."
                  : "Choose a photo below, correct its boxes, record every decision, and use Mark reviewed after all changes are saved."}
              </p>
            </div>
          </section>
        )}

        <section className="project-image-toolbar" aria-label="Image filters">
          <div>
            {IMAGE_FILTERS.map((option) => (
              <button
                key={option.value}
                className={filter === option.value ? "is-active" : ""}
                type="button"
                onClick={() => changeFilter(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span>{total} {total === 1 ? "image" : "images"}</span>
        </section>

        {imageStatus === "loading" ? (
          <ImageGridMessage title="Loading images" detail="Reading managed project images." />
        ) : imageStatus === "error" ? (
          <ImageGridMessage
            title="Images unavailable"
            detail="The image service could not be reached."
            action={<button type="button" onClick={refreshImages}>Try again</button>}
          />
        ) : images.length === 0 ? (
          <ImageGridMessage
            title={filter === "all" ? "No images yet" : "No images in this state"}
            detail={
              filter === "all"
                ? "Upload JPEG or PNG shelf photos to begin annotation."
                : "Choose another status filter to see more images."
            }
            action={
              filter === "all" && canUpload ? (
                <button type="button" onClick={() => setShowUpload(true)}>Upload images</button>
              ) : undefined
            }
          />
        ) : (
          <>
            <section className="project-image-grid" aria-label="Project images">
              {images.map((image) => (
                <button key={image.id} type="button" onClick={() => onOpenImage(project, image)}>
                  <img src={image.thumbnailUrl} alt="" loading="lazy" />
                  <span className={`project-image-status ${image.status}`}>
                    {imageStatusLabel(image.status)}
                  </span>
                  <strong>{image.originalFilename}</strong>
                  <small>{image.width} × {image.height}</small>
                </button>
              ))}
            </section>
            {images.length < total && (
              <button className="project-images-load-more" type="button" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading..." : `Load more (${images.length} of ${total})`}
              </button>
            )}
          </>
        )}
      </main>
    </div>
  );
}

type UploadState = "ready" | "invalid" | "uploading" | "uploaded" | "duplicate" | "failed";

type UploadItem = {
  id: string;
  file: File;
  state: UploadState;
  message: string;
};

function ImageUploadPanel({
  projectId,
  versionId,
  onClose,
  onUploaded,
}: {
  projectId: string;
  versionId: string;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);

  const completedCount = useMemo(
    () => items.filter((item) => ["invalid", "uploaded", "duplicate", "failed"].includes(item.state)).length,
    [items],
  );
  const uploadableCount = items.filter((item) => item.state === "ready" || item.state === "failed").length;

  function selectFiles(files: FileList | File[]) {
    const selected = Array.from(files);
    const available = Math.max(0, 50 - items.length);
    const bounded = selected.slice(0, available);
    setSelectionError(
      selected.length > available ? "Select no more than 50 images at a time." : null,
    );
    setItems((current) => [
      ...current,
      ...bounded.map((file, index) => uploadItem(file, current.length + index)),
    ]);
  }

  function fileInputChanged(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) {
      selectFiles(event.target.files);
      event.target.value = "";
    }
  }

  function filesDropped(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    selectFiles(event.dataTransfer.files);
  }

  function updateItem(id: string, state: UploadState, message: string) {
    setItems((current) =>
      current.map((item) => (item.id === id ? { ...item, state, message } : item)),
    );
  }

  async function uploadFiles() {
    const queue = items.filter((item) => item.state === "ready" || item.state === "failed");
    if (queue.length === 0) {
      return;
    }
    let uploadedAny = false;
    setUploading(true);
    for (const item of queue) {
      updateItem(item.id, "uploading", "Uploading");
      try {
        await uploadProjectImage(projectId, versionId, item.file);
        uploadedAny = true;
        updateItem(item.id, "uploaded", "Uploaded");
      } catch (error) {
        const duplicate = error instanceof ProjectImageApiError && error.code === "duplicate_image";
        updateItem(
          item.id,
          duplicate ? "duplicate" : "failed",
          duplicate ? "Already in this project" : uploadErrorMessage(error),
        );
      }
    }
    setUploading(false);
    if (uploadedAny) {
      onUploaded();
    }
  }

  return (
    <section className="image-upload-panel" aria-label="Upload images">
      <div className="image-upload-panel-heading">
        <div>
          <h2>Upload shelf images</h2>
          <p>JPEG or PNG, up to 50 MiB each. Files are validated before they enter the project.</p>
        </div>
        <button type="button" onClick={onClose} disabled={uploading} aria-label="Close upload panel">×</button>
      </div>
      <label className="image-drop-zone" onDragOver={(event) => event.preventDefault()} onDrop={filesDropped}>
        <input type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" multiple onChange={fileInputChanged} />
        <strong>Drop shelf photos here</strong>
        <span>or choose files from this device</span>
      </label>
      {selectionError && <div className="create-project-error" role="alert">{selectionError}</div>}
      {items.length > 0 && (
        <div className="image-upload-list">
          {items.map((item) => (
            <div key={item.id}>
              <span className={`upload-file-state ${item.state}`} aria-hidden="true" />
              <strong>{item.file.name}</strong>
              <small>{formatFileSize(item.file.size)}</small>
              <span>{item.message}</span>
            </div>
          ))}
        </div>
      )}
      <div className="image-upload-actions">
        <span>{items.length === 0 ? "No files selected" : `${completedCount} of ${items.length} processed`}</span>
        <button type="button" onClick={() => setItems([])} disabled={uploading || items.length === 0}>Clear</button>
        <button className="projects-primary-action" type="button" onClick={() => void uploadFiles()} disabled={uploading || uploadableCount === 0}>
          {uploading ? "Uploading..." : uploadableCount > 0 ? `Upload ${uploadableCount}` : "Upload complete"}
        </button>
      </div>
    </section>
  );
}

function uploadItem(file: File, index: number): UploadItem {
  const extensionAllowed = /\.(jpe?g|png)$/i.test(file.name);
  const typeAllowed = file.type === "image/jpeg" || file.type === "image/png";
  if (!typeAllowed && !extensionAllowed) {
    return {
      id: `${index}-${file.name}-${file.lastModified}`,
      file,
      state: "invalid",
      message: "Use a JPEG or PNG image",
    };
  }
  if (file.size === 0 || file.size > 50 * 1024 * 1024) {
    return {
      id: `${index}-${file.name}-${file.lastModified}`,
      file,
      state: "invalid",
      message: file.size === 0 ? "File is empty" : "File exceeds 50 MiB",
    };
  }
  return {
    id: `${index}-${file.name}-${file.lastModified}`,
    file,
    state: "ready",
    message: "Ready",
  };
}

function uploadErrorMessage(error: unknown): string {
  if (error instanceof ProjectImageApiError) {
    if (error.code === "corrupt_image") {
      return "Image content is corrupt";
    }
    if (error.code === "unsupported_image") {
      return "Image format is unsupported";
    }
    if (error.code === "image_too_large") {
      return "Image dimensions or file size are too large";
    }
    if (error.code === "dataset_version_frozen") {
      return "The working version is frozen";
    }
  }
  return "Upload failed. Retry this file.";
}

function imageStatusLabel(status: ProjectImageStatus): string {
  return {
    unlabeled: "Unannotated",
    pre_labeled: "Pre-labeled",
    in_progress: "In progress",
    labeled: "Labeled",
    reviewed: "Reviewed",
  }[status];
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ImagesMessage({
  title,
  detail,
  onBack,
}: {
  title: string;
  detail: string;
  onBack?: () => void;
}) {
  return (
    <main className="project-images-main">
      <ImageGridMessage
        title={title}
        detail={detail}
        action={onBack ? <button type="button" onClick={onBack}>Back to projects</button> : undefined}
      />
    </main>
  );
}

function ImageGridMessage({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <section className="projects-message project-images-message">
      <img src="/cvsight-mark.svg" alt="" />
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </section>
  );
}
