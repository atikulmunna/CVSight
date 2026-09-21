export type CatalogImportSku = {
  name: string;
  upc: string | null;
  category: string | null;
  subcategory: string | null;
  brand: string | null;
  variant: string | null;
};

const CATALOG_COLUMNS = [
  "name",
  "upc",
  "category",
  "subcategory",
  "brand",
  "variant",
] as const;
const OPTIONAL_COLUMNS = CATALOG_COLUMNS.filter((column) => column !== "name");
const MAX_CATALOG_ROWS = 2500;
const MAX_CATALOG_BYTES = 2 * 1024 * 1024;

export class CatalogCsvError extends Error {}

export function parseCatalogCsv(text: string): CatalogImportSku[] {
  if (!text.trim()) {
    throw new CatalogCsvError("The catalog CSV is empty.");
  }
  if (text.length > MAX_CATALOG_BYTES) {
    throw new CatalogCsvError("The catalog CSV must be 2 MiB or smaller.");
  }
  const rows = parseRows(text);
  const header = rows.shift();
  if (!header) {
    throw new CatalogCsvError("The catalog CSV is missing a header row.");
  }
  header[0] = header[0]?.replace(/^\uFEFF/, "") ?? "";
  const columns = header.map((value) => value.trim().toLowerCase());
  if (columns.some((column) => !column)) {
    throw new CatalogCsvError("Catalog CSV headers cannot be blank.");
  }
  if (new Set(columns).size !== columns.length) {
    throw new CatalogCsvError("Catalog CSV headers must be unique.");
  }
  const unknown = columns.find(
    (column) => !CATALOG_COLUMNS.includes(column as (typeof CATALOG_COLUMNS)[number]),
  );
  if (unknown) {
    throw new CatalogCsvError(`Unsupported catalog column: ${unknown}.`);
  }
  if (!columns.includes("name")) {
    throw new CatalogCsvError("The catalog CSV must include a name column.");
  }

  const imported: CatalogImportSku[] = [];
  const upcs = new Set<string>();
  for (const [index, values] of rows.entries()) {
    const rowNumber = index + 2;
    if (values.every((value) => !value.trim())) {
      continue;
    }
    if (values.length > columns.length && values.slice(columns.length).some((value) => value.trim())) {
      throw new CatalogCsvError(`Row ${rowNumber} has more values than the header.`);
    }
    const fields = new Map(columns.map((column, columnIndex) => [
      column,
      values[columnIndex]?.trim() ?? "",
    ]));
    const name = fields.get("name") ?? "";
    if (!name) {
      throw new CatalogCsvError(`Row ${rowNumber} is missing a SKU name.`);
    }
    validateLength(name, "name", rowNumber);
    for (const column of OPTIONAL_COLUMNS) {
      validateLength(fields.get(column) ?? "", column, rowNumber);
    }
    const upc = fields.get("upc") || null;
    if (upc && !/^(\d{8}|\d{12}|\d{13}|\d{14})$/.test(upc)) {
      throw new CatalogCsvError(`Row ${rowNumber} has an invalid UPC.`);
    }
    if (upc && upcs.has(upc)) {
      throw new CatalogCsvError(`Row ${rowNumber} repeats UPC ${upc}.`);
    }
    if (upc) {
      upcs.add(upc);
    }
    imported.push({
      name,
      upc,
      category: fields.get("category") || null,
      subcategory: fields.get("subcategory") || null,
      brand: fields.get("brand") || null,
      variant: fields.get("variant") || null,
    });
    if (imported.length > MAX_CATALOG_ROWS) {
      throw new CatalogCsvError(`Catalog CSV cannot contain more than ${MAX_CATALOG_ROWS} SKUs.`);
    }
  }
  if (imported.length === 0) {
    throw new CatalogCsvError("The catalog CSV does not contain any SKU rows.");
  }
  return imported;
}

function parseRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let closedQuote = false;

  function finishField() {
    row.push(field);
    field = "";
    closedQuote = false;
  }

  function finishRow() {
    finishField();
    rows.push(row);
    row = [];
  }

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]!;
    if (quoted) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
          closedQuote = true;
        }
      } else {
        field += character;
      }
      continue;
    }
    if (closedQuote && character !== "," && character !== "\r" && character !== "\n") {
      if (character === " " || character === "\t") {
        continue;
      }
      throw new CatalogCsvError("Catalog CSV contains characters after a closing quote.");
    }
    if (character === '"') {
      if (field) {
        throw new CatalogCsvError("Catalog CSV contains an unexpected quote.");
      }
      quoted = true;
    } else if (character === ",") {
      finishField();
    } else if (character === "\r" || character === "\n") {
      finishRow();
      if (character === "\r" && text[index + 1] === "\n") {
        index += 1;
      }
    } else {
      field += character;
    }
  }
  if (quoted) {
    throw new CatalogCsvError("Catalog CSV contains an unclosed quoted value.");
  }
  if (field || row.length > 0 || closedQuote) {
    finishRow();
  }
  return rows;
}

function validateLength(value: string, column: string, rowNumber: number) {
  if (value.length > 255) {
    throw new CatalogCsvError(`Row ${rowNumber} ${column} exceeds 255 characters.`);
  }
}
