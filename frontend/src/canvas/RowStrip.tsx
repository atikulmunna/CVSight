import { cropStyle } from "./crop";
import { skuLabel, type AnnotationBox, type FixtureImage } from "./model";

const CROP_HEIGHT = 56;
const MIN_CROP_WIDTH = 28;
const MAX_CROP_WIDTH = 120;

type RowStripProps = {
  // The shelf row of the selected box, left to right, or null when nothing is selected.
  row: AnnotationBox[] | null;
  images: FixtureImage[];
  selectedIds: ReadonlySet<string>;
  onSelect: (id: string) => void;
};

// The selected box's shelf row as a strip of crops with their names, so a facing named
// differently from its neighbours stands out before it is accepted.
export function RowStrip({ row, images, selectedIds, onSelect }: RowStripProps) {
  if (!row) {
    return (
      <div className="row-strip is-empty">
        <p>Select a box to see its shelf row here.</p>
      </div>
    );
  }
  return (
    <div className="row-strip" role="listbox" aria-label="Facings in this shelf row">
      {row.map((box) => {
        const width = Math.min(
          MAX_CROP_WIDTH,
          Math.max(MIN_CROP_WIDTH, (box.width / box.height) * CROP_HEIGHT),
        );
        const scale = Math.min(CROP_HEIGHT / box.height, width / box.width);
        const name = skuLabel(box);
        return (
          <button
            type="button"
            className={`row-strip-item${selectedIds.has(box.id) ? " is-selected" : ""}`}
            role="option"
            aria-selected={selectedIds.has(box.id)}
            aria-label={`${name}, ${box.state}`}
            title={name}
            data-state={box.state}
            onClick={() => onSelect(box.id)}
            key={box.id}
          >
            <span
              className="row-strip-crop"
              style={{
                width,
                height: CROP_HEIGHT,
                ...cropStyle(box, images[box.imageIndex]!, { width, height: CROP_HEIGHT }, scale),
              }}
            />
            <span className="row-strip-name" style={{ width }}>
              {name}
            </span>
          </button>
        );
      })}
    </div>
  );
}
