import { describe, expect, it } from "vitest";

import { CatalogCsvError, parseCatalogCsv } from "./catalogCsv";

describe("catalog CSV", () => {
  it("parses quoted values and optional columns", () => {
    const csv = [
      "name,upc,category,brand,variant",
      '"Aurora Cola, Zero",012345678905,Beverages,Aurora,"330 ml can"',
      "Northstar Water,,Beverages,Northstar,1 L",
    ].join("\r\n");

    expect(parseCatalogCsv(csv)).toEqual([
      {
        name: "Aurora Cola, Zero",
        upc: "012345678905",
        category: "Beverages",
        subcategory: null,
        brand: "Aurora",
        variant: "330 ml can",
      },
      {
        name: "Northstar Water",
        upc: null,
        category: "Beverages",
        subcategory: null,
        brand: "Northstar",
        variant: "1 L",
      },
    ]);
  });

  it("rejects unsupported headers and invalid UPCs", () => {
    expect(() => parseCatalogCsv("name,price\nAurora,10")).toThrow(
      new CatalogCsvError("Unsupported catalog column: price."),
    );
    expect(() => parseCatalogCsv("name,upc\nAurora,123")).toThrow(
      new CatalogCsvError("Row 2 has an invalid UPC."),
    );
  });

  it("rejects duplicate UPCs and missing names", () => {
    expect(() => parseCatalogCsv(
      "name,upc\nAurora,012345678905\nNorthstar,012345678905",
    )).toThrow(new CatalogCsvError("Row 3 repeats UPC 012345678905."));
    expect(() => parseCatalogCsv("name,brand\n,Aurora")).toThrow(
      new CatalogCsvError("Row 2 is missing a SKU name."),
    );
  });

  it("rejects malformed quoting and oversized catalogs", () => {
    expect(() => parseCatalogCsv('name\n"Unclosed')).toThrow(
      new CatalogCsvError("Catalog CSV contains an unclosed quoted value."),
    );
    const rows = Array.from({ length: 2501 }, (_, index) => `SKU ${index}`);
    expect(() => parseCatalogCsv(["name", ...rows].join("\n"))).toThrow(
      new CatalogCsvError("Catalog CSV cannot contain more than 2500 SKUs."),
    );
  });
});
