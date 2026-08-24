import type { Sku } from "./model";

const BRANDS = [
  "Aurora",
  "Northstar",
  "Meadow",
  "Harvest",
  "Verde",
  "Nimbus",
  "Sunfield",
  "Kettle",
];

const PRODUCTS = [
  "Cola",
  "Hydration Water",
  "Oat Crisps",
  "Whole Milk",
  "Orange Juice",
  "Sea Salt Chips",
];

export const DEMO_SKUS: Sku[] = [
  {
    id: "00000000-0000-0000-0000-000000000001",
    name: "Unknown / Other",
    upc: null,
    category: null,
    subcategory: null,
    brand: null,
    variant: null,
    isUnknown: true,
    status: "active",
    mergedIntoId: null,
    referenceImages: [],
  },
  ...Array.from({ length: 2_000 }, (_, index): Sku => {
    const brand = BRANDS[index % BRANDS.length]!;
    const product = PRODUCTS[index % PRODUCTS.length]!;
    const size = [250, 330, 500, 750][index % 4]!;
    const pack = (index % 12) + 1;
    return {
      id: `demo-sku-${String(index + 1).padStart(4, "0")}`,
      name: `${brand} ${product} ${size}ml ${pack} pack`,
      upc: String(10_000_000_000 + index).padStart(12, "0"),
      category: product.includes("Chips") || product.includes("Crisps")
        ? "Snacks"
        : "Beverages",
      subcategory: product,
      brand,
      variant: `${size}ml ${pack} pack`,
      isUnknown: false,
      status: "active",
      mergedIntoId: null,
      referenceImages: [],
    };
  }),
];
