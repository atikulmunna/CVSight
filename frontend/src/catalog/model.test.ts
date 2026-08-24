import { describe, expect, it } from "vitest";

import { DEMO_SKUS } from "./demoCatalog";
import { hierarchyPath, searchSkus } from "./model";

describe("SKU catalog search", () => {
  it("finds hard variants from prefixes and a one-character omission", () => {
    const skus = [
      {
        ...DEMO_SKUS[1]!,
        name: "Aurora Cola Zero 330ml Can",
        brand: "Aurora",
        variant: "Zero 330ml can",
      },
      {
        ...DEMO_SKUS[2]!,
        name: "Aurora Cola Classic 500ml Bottle",
        brand: "Aurora",
        variant: "Classic 500ml bottle",
      },
    ];

    expect(searchSkus(skus, "aur zro 330")[0]?.name).toBe(
      "Aurora Cola Zero 330ml Can",
    );
  });

  it("keeps the explicit unknown target first when no query is present", () => {
    expect(searchSkus(DEMO_SKUS.slice(0, 10), "")[0]?.isUnknown).toBe(true);
  });

  it("searches a 2,001 SKU fixture within the interaction budget", () => {
    const startedAt = performance.now();
    const results = searchSkus(DEMO_SKUS, "north hyd 330 2");
    const elapsed = performance.now() - startedAt;

    expect(results.length).toBeGreaterThan(0);
    expect(elapsed).toBeLessThan(100);
  });

  it("formats hierarchy without empty separators", () => {
    expect(
      hierarchyPath({
        ...DEMO_SKUS[1]!,
        category: "Beverages",
        subcategory: null,
        brand: "Aurora",
        variant: "Zero",
      }),
    ).toBe("Beverages / Aurora / Zero");
  });
});
