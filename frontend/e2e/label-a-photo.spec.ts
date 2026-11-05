import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const SHELF_PHOTO = path.join(path.dirname(fileURLToPath(import.meta.url)), "shelf.png");

async function signIn(page: Page, password: string) {
  await page.goto("/");
  await page.getByLabel("Username").fill("e2e-owner");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

function accountPassword(): string {
  const password = process.env.CVSIGHT_E2E_PASSWORD;
  if (!password) {
    throw new Error("Run the browser tests with scripts/e2e.ps1.");
  }
  return password;
}

test("a wrong password keeps the sign-in page open", async ({ page }) => {
  await signIn(page, "not-the-password");

  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});

test("a box drawn on an uploaded photo is still there after a reload", async ({ page }) => {
  await signIn(page, accountPassword());

  await page.getByRole("button", { name: "New project" }).first().click();
  await page.getByLabel("Project name").fill(`Browser test ${Date.now()}`);
  await page.getByRole("button", { name: "Create project" }).click();

  await page.getByRole("navigation", { name: "Project" }).getByRole("button", { name: "Images" }).click();
  await page.getByRole("button", { name: "Upload images" }).first().click();
  await page.locator('input[type="file"]').setInputFiles(SHELF_PHOTO);
  await page.getByRole("button", { name: "Upload 1" }).click();
  await expect(page.getByRole("button", { name: "Upload complete" })).toBeVisible();
  await page.getByRole("button", { name: "Close upload panel" }).click();

  await page.getByRole("region", { name: "Project images" }).getByRole("button").first().click();
  const photo = page.getByLabel("Shelf image");
  await expect(photo).toBeVisible();

  await page.getByRole("button", { name: "Draw box" }).click();
  const area = await photo.boundingBox();
  if (!area) {
    throw new Error("The photo has no position on the canvas.");
  }
  await page.mouse.move(area.x + area.width * 0.3, area.y + area.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(area.x + area.width * 0.5, area.y + area.height * 0.6, { steps: 5 });
  await page.mouse.up();

  const boxes = page.locator('[data-class="product"]');
  await expect(boxes).toHaveCount(1);
  await expect(page.getByText("All changes saved")).toBeVisible();

  await page.reload();

  await expect(photo).toBeVisible();
  await expect(boxes).toHaveCount(1);
});
