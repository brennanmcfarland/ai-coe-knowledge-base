import { expect, type Page } from '@playwright/test'

export interface InterviewOptions {
  /** Option value of the domain question (oncology makes the fake model generate steps). */
  domain?: string
  /** Free text for the app description (`simulate-llm-failure` makes the model fail once). */
  description?: string
}

export async function next(page: Page) {
  await page.getByTestId('interview-next').click()
}

export async function expectSection(page: Page, section: string) {
  await expect(page.getByTestId('interview-section')).toHaveAttribute('data-section', section, { timeout: 15_000 })
}

/** Answers the first (app) section of an interview that's open on the page. */
export async function answerAppSection(page: Page, description?: string) {
  await page.getByTestId('opt-app_type-research-tool').click()
  await expect(page.getByTestId('opt-app_type-research-tool')).toHaveAttribute('aria-pressed', 'true')
  if (description) await page.getByTestId('text-app_description').fill(description)
}

/** Answers the rest of the interview (domain onwards) and waits for the project graph. */
export async function finishInterview(page: Page, domain = 'general') {
  await expectSection(page, 'domain')
  await page.getByTestId(`opt-domain-${domain}`).click()
  await next(page)
  // The scripted model asks one follow-up in the domain section.
  await expect(page.getByTestId('followups-intro')).toBeVisible({ timeout: 15_000 })
  await next(page)

  await expectSection(page, 'data')
  await page.getByTestId('opt-data_types-genomic').click()
  await page.getByTestId('opt-data_access-public').click()
  await next(page)

  await expectSection(page, 'risk')
  await page.getByTestId('opt-sensitivity-no-patient-data').click()
  await page.getByTestId('opt-deployment-sandbox').click()
  await page.getByTestId('opt-users-researchers').click()
  await next(page)
  await page.waitForURL(/\/projects\/\d+$/, { timeout: 30_000 })
  await expect(page.getByTestId('dag')).toBeVisible()
}

export async function startInterview(page: Page, name: string) {
  await page.goto('/projects')
  await page.getByTestId('project-name').fill(name)
  await page.getByTestId('start-interview').click()
  await page.waitForURL(/\/interviews\/[0-9a-f]+$/)
  await expectSection(page, 'app')
}

/** Creates a project through the interview and returns its id; leaves the page on its graph. */
export async function createProject(page: Page, name: string, opts: InterviewOptions = {}): Promise<number> {
  await startInterview(page, name)
  await answerAppSection(page, opts.description)
  await next(page)
  await finishInterview(page, opts.domain)
  return Number(new URL(page.url()).pathname.split('/').pop())
}

export async function setEditMode(page: Page, on: boolean) {
  const toggle = page.getByTestId('edit-toggle')
  if ((await toggle.getAttribute('aria-checked')) !== String(on)) await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', String(on))
}
