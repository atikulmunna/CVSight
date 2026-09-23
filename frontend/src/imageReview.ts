import { isUuid } from "./canvas/liveFixture";
import type { SaveStatus } from "./canvas/useAnnotationAutosave";

export type ReviewButtonState = {
  enabled: boolean;
  label: string;
};

export async function loadImageReviewed(
  imageId: string,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const response = await fetcher(`/api/images/${encodeURIComponent(checkedId(imageId))}`, {
    headers: { Accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok || !body || typeof body !== "object") {
    throw new Error("image review status is unavailable");
  }
  return (body as Record<string, unknown>).status === "reviewed";
}

export async function markImageReviewed(
  imageId: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher(
    `/api/images/${encodeURIComponent(checkedId(imageId))}/reviewed`,
    { method: "POST", headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    throw new Error(
      response.status === 409
        ? "Accept or reject every active box first."
        : "Image review could not be saved.",
    );
  }
}

// The server refuses review while any box is undecided, so the button explains what
// is still in the way instead of failing after the click.
export function reviewButtonState(
  {
    reviewed,
    busy,
    unresolvedCount,
    saveStatus,
  }: {
    reviewed: boolean;
    busy: boolean;
    unresolvedCount: number;
    saveStatus: SaveStatus;
  },
  readyLabel = "Mark reviewed",
): ReviewButtonState {
  const label = reviewed
    ? "Image reviewed"
    : unresolvedCount > 0
      ? `${unresolvedCount} decisions remaining`
      : saveStatus === "saving"
        ? "Saving changes"
        : saveStatus === "conflict"
          ? "Resolve save conflict"
          : saveStatus === "offline"
            ? "Waiting for connection"
            : readyLabel;
  return {
    enabled: !busy && !reviewed && unresolvedCount === 0 && saveStatus === "saved",
    label,
  };
}

function checkedId(imageId: string): string {
  if (!isUuid(imageId)) {
    throw new Error("image id is invalid");
  }
  return imageId;
}
