import { expect, test } from "@playwright/test";

/**
 * The demo path, end to end against a running stack.
 *
 *   docker compose up --build
 *   docker compose exec api python -m learnflow.seed
 *   npm run e2e
 *
 * These are skipped unless E2E_RUN=1 so CI does not fail on a machine with no stack up.
 */
const RUN = process.env.E2E_RUN === "1";
const EMAIL = process.env.E2E_EMAIL ?? "admin@learnflow.dev";
const PASSWORD = process.env.E2E_PASSWORD ?? "LearnFlow!2026";

test.skip(!RUN, "Set E2E_RUN=1 with the stack running and seeded");

test.beforeEach(async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
});

test("dashboard shows real seeded metrics", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  const learners = page.getByText("Learners", { exact: true }).locator("..");
  await expect(learners).not.toContainText("—");
});

test("knowledge base answers are cited", async ({ page }) => {
  await page.goto("/dashboard/assistant");
  await page
    .getByLabel("Question")
    .fill("What is the approval code for a remote work request?");
  await page.getByRole("button", { name: "Ask" }).click();

  await expect(page.getByText("Sources")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/remote-work-policy\.md/)).toBeVisible();
});

test("adding a learner triggers the onboarding workflow", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;
  await page.goto("/dashboard/learners");
  await page.getByRole("button", { name: "Add learner" }).click();
  await page.getByLabel("Full name").fill("E2E Learner");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Add learner" }).last().click();

  await expect(page.getByText(email)).toBeVisible({ timeout: 15_000 });

  await page.goto("/dashboard/executions");
  await expect(page.getByRole("heading", { name: "Workflow executions" })).toBeVisible();
  await expect(page.locator("tbody tr").first()).toBeVisible();
});

test("evaluation review queue enforces human sign-off", async ({ page }) => {
  await page.goto("/dashboard/reviews");
  await expect(page.getByRole("heading", { name: "Evaluation review" })).toBeVisible();
  const cards = page.locator("[role=alert], .rounded-lg");
  await expect(cards.first()).toBeVisible();
});

test("cost dashboard reports non-zero spend after seeding", async ({ page }) => {
  await page.goto("/dashboard/usage");
  await expect(page.getByRole("heading", { name: "AI usage and cost" })).toBeVisible();
  await expect(page.getByText("Estimated cost").locator("..")).not.toContainText("—");
});
