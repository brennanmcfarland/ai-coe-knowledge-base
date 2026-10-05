import { expect, test, type Page } from '@playwright/test'
import { createProject, setEditMode } from './helpers'

async function connect(page: Page, source: string, target: string) {
  const from = page.getByTestId(`dag-node-${source}`).locator('.react-flow__handle-bottom')
  const to = page.getByTestId(`dag-node-${target}`).locator('.react-flow__handle-top')
  const a = (await from.boundingBox())!
  const b = (await to.boundingBox())!
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 15 })
  await page.mouse.up()
}

function edge(page: Page, source: string, target: string) {
  return page.locator(`.react-flow__edge[data-id="${source}->${target}"]`)
}

test.afterEach(async ({ page }) => {
  await setEditMode(page, false)
})

test('rename a step in edit mode', async ({ page }) => {
  const pid = await createProject(page, 'Rename')
  await setEditMode(page, true)
  await page.goto(`/projects/${pid}/nodes/write-docs`)
  const title = page.getByLabel('Step title')
  await title.fill('Write documentation')
  await page.getByRole('button', { name: 'Rename' }).click()
  await expect(page.getByTestId('node-title')).toHaveText('Write documentation')
  await expect(page.getByTestId('node-edit-tools')).toContainText('Locked against rebuilds: title')
})

test('add an edge by dragging, and reject one that would create a cycle', async ({ page }) => {
  await createProject(page, 'Edges')
  await setEditMode(page, true)
  await expect(edge(page, 'monitoring', 'write-docs')).toHaveCount(0)

  await connect(page, 'monitoring', 'write-docs')
  await expect(edge(page, 'monitoring', 'write-docs')).toHaveCount(1)

  await connect(page, 'deploy', 'define-goals')
  await expect(page.getByTestId('edit-error')).toContainText('would create a cycle')
  await expect(edge(page, 'deploy', 'define-goals')).toHaveCount(0)
})

test('merging nodes moves progress to the surviving node', async ({ page }) => {
  const pid = await createProject(page, 'Merge')
  await page.goto(`/projects/${pid}/nodes/observability`)
  await page.getByTestId('check-tracing').click()
  await page.getByTestId('mark-complete').click()
  await expect(page.getByTestId('node-state')).toHaveText('Complete')

  await setEditMode(page, true)
  await page.getByTestId('merge-button').click()
  await page.getByTestId('merge-target').click()
  await page.getByRole('option', { name: 'Monitoring' }).click()
  await page.getByTestId('merge-confirm').click()

  await expect(page).toHaveURL(/\/nodes\/monitoring$/)
  await setEditMode(page, false)
  await expect(page.getByTestId('node-state')).toHaveText('Complete')
  await expect(page.getByTestId('check-tracing')).toHaveAttribute('data-state', 'checked')
  await expect(page.getByTestId('check-dashboards')).toHaveAttribute('data-state', 'unchecked')

  await page.goto(`/projects/${pid}`)
  await expect(page.getByTestId('dag-node-observability')).toHaveCount(0)
})

test('accepting a proposal makes it part of the learner graph', async ({ page }) => {
  await createProject(page, 'Proposals')
  await expect(page.getByTestId('dag-node-load-testing')).toHaveCount(0)

  await setEditMode(page, true)
  await expect(page.getByTestId('dag-node-load-testing')).toContainText('proposed')
  await page.getByTestId('proposals-button').click()
  await page.getByTestId('proposal-node-load-testing').getByRole('button', { name: 'Accept' }).click()
  await page.getByTestId('proposal-edge-security-review-load-testing').getByRole('button', { name: 'Accept' }).click()
  await expect(page.getByTestId('proposals-button')).toContainText('0')
  await page.keyboard.press('Escape')

  await setEditMode(page, false)
  await expect(page.getByTestId('dag-node-load-testing')).toHaveAttribute('data-state', 'locked')
  await expect(edge(page, 'security-review', 'load-testing')).toHaveCount(1)
})

test('broken citations are flagged and can be removed', async ({ page }) => {
  const pid = await createProject(page, 'Broken citations')
  await setEditMode(page, true)
  await page.goto(`/projects/${pid}/nodes/security-review`)
  await expect(page.getByTestId('broken-citation-1')).toBeVisible()
  await page.getByRole('button', { name: 'Remove citation 1' }).click()
  await expect(page.getByTestId('broken-citation-1')).toHaveCount(0)
  await expect(page.getByTestId('node-edit-tools')).toContainText('citations')
})
