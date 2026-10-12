import { expect, test } from '@playwright/test'
import {
  answerAppSection,
  createProject,
  expectSection,
  finishInterview,
  next,
  setEditMode,
  startInterview,
} from './helpers'

test.beforeEach(async ({ page }) => {
  await page.goto('/projects')
  await setEditMode(page, false)
})

test('an oncology interview adds a cited terminology step and flags uncited ones', async ({ page }) => {
  const pid = await createProject(page, 'Oncology app', { domain: 'oncology' })
  await expect(page.getByTestId('dag-node-cardiology-registries')).toHaveCount(0) // other domain
  await expect(page.getByTestId('dag-node-define-goals')).toHaveAttribute('data-state', 'frontier')

  const terms = page.getByTestId('dag-node-gen.acquire-oncology-terminologies')
  await expect(terms).toHaveAttribute('data-scope', 'project')
  await expect(page.getByTestId('not-from-kb-gen.obtain-terminology-licenses')).toBeVisible()
  await expect(page.getByTestId('not-from-kb-gen.acquire-oncology-terminologies')).toHaveCount(0)

  await page.goto(`/projects/${pid}/nodes/gen.acquire-oncology-terminologies`)
  await expect(page.getByTestId('project-step-badge')).toBeVisible()
  await expect(page.getByTestId('not-from-kb')).toHaveCount(0)
  await page.getByTestId('source-1').click()
  await expect(page.getByTestId('citation-panel')).toContainText('ICD-O-3')

  await page.goto(`/projects/${pid}/nodes/gen.obtain-terminology-licenses`)
  await expect(page.getByTestId('not-from-kb')).toBeVisible()
})

test('an unfinished interview survives a reload and can be resumed', async ({ page }) => {
  await startInterview(page, 'Resumable')
  await answerAppSection(page)
  await next(page)
  await expectSection(page, 'domain')
  const url = page.url()

  await page.reload()
  await expectSection(page, 'domain')
  await page.goto('/projects')
  await expect(page.getByTestId('project-list')).not.toContainText('Resumable') // no project yet
  await page.getByTestId('draft-list').getByText('Resumable').click()
  await expect(page).toHaveURL(url)
  await finishInterview(page)
  await expect(page.getByTestId('project-title')).toHaveText('Resumable')
})

test('a model failure keeps the answers and Retry continues', async ({ page }) => {
  await startInterview(page, 'Flaky model')
  await answerAppSection(page, 'simulate-llm-failure')
  await next(page)
  await expect(page.getByTestId('interview-failed')).toContainText('Simulated model failure')
  await expect(page.getByTestId('opt-app_type-research-tool')).toHaveAttribute('aria-pressed', 'true')
  await page.getByTestId('interview-retry').click()
  await finishInterview(page)
  await expect(page.getByTestId('project-title')).toHaveText('Flaky model')
})

test('re-tailoring shows changes as proposals until accepted', async ({ page }) => {
  await createProject(page, 'Retailor me')
  await expect(page.getByTestId('dag-node-gen.acquire-oncology-terminologies')).toHaveCount(0)

  await page.getByTestId('retailor').click()
  await page.waitForURL(/\/interviews\//)
  // Answers are prefilled; switch the domain to oncology.
  await expect(page.getByTestId('opt-app_type-research-tool')).toHaveAttribute('aria-pressed', 'true')
  await next(page)
  await expectSection(page, 'domain')
  await page.getByTestId('opt-domain-general').click() // untick
  await page.getByTestId('opt-domain-oncology').click()
  await next(page)
  await expect(page.getByTestId('followups-intro')).toBeVisible()
  await next(page)
  await expectSection(page, 'data') // prefilled: keep the answers
  await next(page)
  await expectSection(page, 'risk')
  await expect(page.getByTestId('interview-next')).toHaveText('Next')
  await next(page)
  await page.waitForURL(/\/projects\/\d+$/)

  await expect(page.getByTestId('dag-node-gen.acquire-oncology-terminologies')).toHaveCount(0)
  await page.getByTestId('review-changes-button').click()
  const proposal = page.getByTestId('project-proposal-add_node:gen.acquire-oncology-terminologies')
  await proposal.getByRole('button', { name: 'Accept' }).click()
  await expect(proposal).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('dag-node-gen.acquire-oncology-terminologies')).toBeVisible()
})

test('a project-specific step can be promoted to the global ontology', async ({ page }) => {
  const pid = await createProject(page, 'Promoter', { domain: 'oncology' })
  await setEditMode(page, true)
  await page.goto(`/projects/${pid}/nodes/gen.obtain-terminology-licenses`)
  await page.getByTestId('promote-node').click()
  await expect(page.getByTestId('promoted-note')).toContainText('obtain-terminology-licenses')
  await page.goto(`/projects/${pid}`)
  await expect(page.getByTestId('proposals-button')).toBeVisible()
  await setEditMode(page, false)
})
