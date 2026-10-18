import { DEFAULT_DISPLAY, DISPLAY_RANGES, type DisplaySettings } from "./display";
import type { AnnotationClass } from "./model";

const CLASS_LABELS: Array<[AnnotationClass, string]> = [
  ["product", "Products"],
  ["gap", "Gaps"],
  ["shelf_label", "Shelf labels"],
];

const SLIDERS: Array<[keyof typeof DISPLAY_RANGES, string]> = [
  ["brightness", "Brightness"],
  ["contrast", "Contrast"],
  ["boxFill", "Box fill"],
];

export function DisplayPanel({
  settings,
  hideAll,
  onChange,
  onHideAllChange,
}: {
  settings: DisplaySettings;
  hideAll: boolean;
  onChange: (settings: DisplaySettings) => void;
  onHideAllChange: (hideAll: boolean) => void;
}) {
  function toggleClass(classType: AnnotationClass, shown: boolean) {
    onChange({
      ...settings,
      hiddenClasses: shown
        ? settings.hiddenClasses.filter((hidden) => hidden !== classType)
        : [...settings.hiddenClasses, classType],
    });
  }

  return (
    <section className="display-panel" aria-label="Display">
      <div className="display-heading">
        <span className="eyebrow">Display</span>
        <button
          type="button"
          onClick={() => {
            onChange(DEFAULT_DISPLAY);
            onHideAllChange(false);
          }}
        >
          Reset
        </button>
      </div>
      {SLIDERS.map(([field, label]) => (
        <label className="display-slider" key={field}>
          {label}
          <input
            type="range"
            min={DISPLAY_RANGES[field].min}
            max={DISPLAY_RANGES[field].max}
            step={5}
            value={settings[field]}
            onChange={(event) =>
              onChange({ ...settings, [field]: Number(event.currentTarget.value) })
            }
          />
          <output>{settings[field]}%</output>
        </label>
      ))}
      <div className="display-toggles" role="group" aria-label="Boxes shown">
        {CLASS_LABELS.map(([classType, label]) => (
          <label key={classType}>
            <input
              type="checkbox"
              checked={!hideAll && !settings.hiddenClasses.includes(classType)}
              disabled={hideAll}
              onChange={(event) => toggleClass(classType, event.currentTarget.checked)}
            />
            {label}
          </label>
        ))}
        <label>
          <input
            type="checkbox"
            checked={hideAll}
            onChange={(event) => onHideAllChange(event.currentTarget.checked)}
          />
          Hide all <kbd>O</kbd>
        </label>
      </div>
    </section>
  );
}
