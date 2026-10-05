import { expect, type Page } from '@playwright/test'

/** Creates a project through the UI and returns its id; leaves the page on its graph. */
export async function createProject(page: Page, name: string): Promise<number> {
  await page.goto('/projects')
  await page.getByTestId('project-name').fill(name)
  await page.getByRole('button', { name: 'Create' }).click()
  await page.waitForURL(/\/projects\/\d+$/)
  await expect(page.getByTestId('dag')).toBeVisible()
  return Number(new URL(page.url()).pathname.split('/').pop())
}

export async function setEditMode(page: Page, on: boolean) {
  const toggle = page.getByTestId('edit-toggle')
  if ((await toggle.getAttribute('aria-checked')) !== String(on)) await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', String(on))
}
