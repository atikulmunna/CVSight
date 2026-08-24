export type SkuStatus = "active" | "deprecated" | "merged";

export type SkuReferenceImage = {
  id: string;
  originalFilename: string;
  thumbnailUrl: string;
};

export type Sku = {
  id: string;
  name: string;
  upc: string | null;
  category: string | null;
  subcategory: string | null;
  brand: string | null;
  variant: string | null;
  isUnknown: boolean;
  status: SkuStatus;
  mergedIntoId: string | null;
  referenceImages: SkuReferenceImage[];
};

export function searchSkus(skus: Sku[], query: string): Sku[] {
  const terms = normalize(query).split(" ").filter(Boolean);
  if (terms.length === 0) {
    return [...skus].sort(defaultOrder);
  }
  return skus
    .map((sku) => ({ sku, score: searchScore(sku, terms) }))
    .filter(
      (candidate): candidate is { sku: Sku; score: number } =>
        candidate.score !== null,
    )
    .sort(
      (left, right) =>
        right.score - left.score || defaultOrder(left.sku, right.sku),
    )
    .map((candidate) => candidate.sku);
}

export function hierarchyPath(sku: Sku): string {
  return [sku.category, sku.subcategory, sku.brand, sku.variant]
    .filter(Boolean)
    .join(" / ");
}

function searchScore(sku: Sku, terms: string[]): number | null {
  const fields = [
    sku.name,
    sku.upc,
    sku.category,
    sku.subcategory,
    sku.brand,
    sku.variant,
  ]
    .filter((value): value is string => Boolean(value))
    .map(normalize);
  const words = fields.flatMap((field) => field.split(" "));
  let score = 0;
  for (const term of terms) {
    const termScore = bestTermScore(term, fields, words);
    if (termScore === 0) {
      return null;
    }
    score += termScore;
  }
  return score + (sku.isUnknown ? -1 : 0);
}

function bestTermScore(
  term: string,
  fields: string[],
  words: string[],
): number {
  if (fields.some((field) => field === term)) {
    return 100;
  }
  if (words.some((word) => word === term)) {
    return 80;
  }
  if (words.some((word) => word.startsWith(term))) {
    return 60;
  }
  if (
    term.length >= 3 &&
    words.some((word) => Math.abs(word.length - term.length) <= 1 && editDistanceAtMostOne(word, term))
  ) {
    return 40;
  }
  if (term.length >= 2 && words.some((word) => isSubsequence(term, word))) {
    return 20;
  }
  return fields.some((field) => field.includes(term)) ? 10 : 0;
}

function editDistanceAtMostOne(left: string, right: string): boolean {
  if (left === right) {
    return true;
  }
  if (Math.abs(left.length - right.length) > 1) {
    return false;
  }
  if (left.length === right.length) {
    let differences = 0;
    for (let index = 0; index < left.length; index += 1) {
      differences += left[index] === right[index] ? 0 : 1;
    }
    return differences <= 1;
  }
  const shorter = left.length < right.length ? left : right;
  const longer = left.length < right.length ? right : left;
  let shortIndex = 0;
  let longIndex = 0;
  let skipped = false;
  while (shortIndex < shorter.length && longIndex < longer.length) {
    if (shorter[shortIndex] === longer[longIndex]) {
      shortIndex += 1;
      longIndex += 1;
      continue;
    }
    if (skipped) {
      return false;
    }
    skipped = true;
    longIndex += 1;
  }
  return true;
}

function isSubsequence(needle: string, value: string): boolean {
  let index = 0;
  for (const character of value) {
    if (character === needle[index]) {
      index += 1;
    }
    if (index === needle.length) {
      return true;
    }
  }
  return false;
}

function normalize(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function defaultOrder(left: Sku, right: Sku): number {
  return (
    Number(right.isUnknown) - Number(left.isUnknown) ||
    left.name.localeCompare(right.name) ||
    left.id.localeCompare(right.id)
  );
}
