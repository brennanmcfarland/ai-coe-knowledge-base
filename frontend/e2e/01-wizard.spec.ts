import { expect, test } from '@playwright/test'
import { createProject, setEditMode } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.goto('/projects')
  await setEditMode(page, false)
})

test('creating a project shows the DAG with only the root on the frontier', async ({ page }) => {
  await createProject(page, 'Wizard basics')
  await expect(page.getByTestId('project-title')).toHaveText('Wizard basics')
  await expect(page.getByTestId('dag-node-define-goals')).toHaveAttribute('data-state', 'frontier')
  await expect(page.getByTestId('dag-node-procure-data')).toHaveAttribute('data-state', 'locked')
  // Proposals are hidden outside edit mode.
  await expect(page.getByTestId('dag-node-load-testing')).toHaveCount(0)
})

test('clicking a DAG node opens its page', async ({ page }) => {
  await createProject(page, 'Navigation')
  await page.getByTestId('dag-node-procure-data').click()
  await expect(page).toHaveURL(/\/nodes\/procure-data$/)
  await expect(page.getByTestId('node-title')).toHaveText('Procure data')
  await expect(page.getByTestId('node-state')).toHaveText('Prerequisites pending')
})

test('checklist ticks survive navigating away and back', async ({ page }) => {
  const pid = await createProject(page, 'Checklist persistence')
  await page.getByTestId('dag-node-define-goals').click()
  await page.getByTestId('check-goals-written').click()
  await expect(page.getByTestId('check-goals-written')).toHaveAttribute('data-state', 'checked')

  await page.getByTestId('back-to-graph').click()
  await page.getByTestId('dag-node-system-design').click()
  await expect(page.getByTestId('node-title')).toHaveText('System design')
  await page.goto(`/projects/${pid}/nodes/define-goals`)
  await expect(page.getByTestId('check-goals-written')).toHaveAttribute('data-state', 'checked')
  await expect(page.getByTestId('check-metrics-agreed')).toHaveAttribute('data-state', 'unchecked')

  // Survives a full reload too: state lives in the backend.
  await page.reload()
  await expect(page.getByTestId('check-goals-written')).toHaveAttribute('data-state', 'checked')
})

test('marking a node complete advances the frontier', async ({ page }) => {
  const pid = await createProject(page, 'Frontier')
  await page.goto(`/projects/${pid}/nodes/define-goals`)
  await page.getByTestId('mark-complete').click()
  await expect(page.getByTestId('node-state')).toHaveText('Complete')

  await page.getByTestId('back-to-graph').click()
  await expect(page.getByTestId('dag-node-define-goals')).toHaveAttribute('data-state', 'complete')
  await expect(page.getByTestId('dag-node-procure-data')).toHaveAttribute('data-state', 'frontier')
  await expect(page.getByTestId('dag-node-system-design')).toHaveAttribute('data-state', 'frontier')
  await expect(page.getByTestId('dag-node-deploy')).toHaveAttribute('data-state', 'locked')
  await expect(page.getByTestId('progress-count')).toContainText('1 /')
})

test('projects keep separate progress', async ({ page }) => {
  const a = await createProject(page, 'Project A')
  await page.goto(`/projects/${a}/nodes/define-goals`)
  await page.getByTestId('mark-complete').click()
  await expect(page.getByTestId('node-state')).toHaveText('Complete')

  await createProject(page, 'Project B')
  await expect(page.getByTestId('dag-node-define-goals')).toHaveAttribute('data-state', 'frontier')
})

test('a citation opens the side panel with a link to ClickUp', async ({ page }) => {
  const pid = await createProject(page, 'Citations')
  await page.goto(`/projects/${pid}/nodes/define-goals`)
  await page.getByTestId('cite-marker-2').click()
  const panel = page.getByTestId('citation-panel')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('Define measurable success metrics.')
  const link = panel.getByTestId('open-in-clickup')
  await expect(link).toHaveAttribute('href', 'https://app.clickup.com/1/v/dc/d1/p2')
  await expect(link).toHaveAttribute('target', '_blank')
})
