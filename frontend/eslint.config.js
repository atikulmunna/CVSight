import eslint from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// The functions that were already over the limit when it was added. Each ceiling stops
// its file getting worse; lower it when the file is split up.
const COMPLEXITY_CEILINGS = {
  "src/App.tsx": 51,
  "src/canvas/AnnotationWorkspace.tsx": 73,
  "src/canvas/PropagationWorkspace.tsx": 17,
  "src/canvas/useAnnotationAutosave.ts": 16,
  "src/projects/ProjectImagesPage.tsx": 34,
  "src/projects/ProjectOverviewPage.tsx": 17,
  "src/projects/catalogCsv.ts": 30,
  "src/review/ReviewWorkspace.tsx": 23,
};

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
  {
    extends: [eslint.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      complexity: ["error", 15],
    },
  },
  ...Object.entries(COMPLEXITY_CEILINGS).map(([file, ceiling]) => ({
    files: [file],
    rules: { complexity: ["error", ceiling] },
  })),
);
