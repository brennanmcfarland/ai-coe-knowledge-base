import { expect, test } from '@playwright/test'

test('login page walks through setup, then offers ClickUp login', async ({ page }) => {
  await page.goto('/login')
  const form = page.getByTestId('setup-form')
  await expect(form).toBeVisible()
  await expect(form).toContainText('/api/auth/callback')

  await page.getByLabel('Client ID').fill('test-client')
  await page.getByLabel('Client secret').fill('test-secret')
  await page.getByRole('button', { name: 'Save credentials' }).click()

  const login = page.getByTestId('login-button')
  await expect(login).toBeVisible()
  await expect(login).toHaveAttribute('href', '/api/auth/login')
})

test('login errors from the OAuth callback are shown', async ({ page }) => {
  await page.goto('/api/auth/callback?code=x&state=forged')
  await expect(page).toHaveURL(/\/login\?error=/)
  await expect(page.getByTestId('login-error')).toContainText('Invalid or expired OAuth state')
})

test('sync without login sends the user to the login page', async ({ page }) => {
  await page.goto('/projects')
  await page.getByRole('button', { name: 'Sync from ClickUp' }).click()
  await expect(page).toHaveURL(/\/login\?next=sync$/)
})
