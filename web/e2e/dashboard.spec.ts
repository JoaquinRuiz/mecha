import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page, test } from '@playwright/test'

const URL = readFileSync(join(import.meta.dirname, '.session-url'), 'utf8').trim()
const BASE = URL.split('#')[0] as string

/** Fail on any console error, page error or CSP violation (covers the nonce on upgrades). */
async function guard(page: Page): Promise<string[]> {
  const problems: string[] = []
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) => {
      console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`)
    })
  })
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(m.text())
  })
  page.on('pageerror', (e) => problems.push(e.message))
  return problems
}

async function load(page: Page) {
  await page.goto(URL)
  await expect(page.getByTestId('credential-card')).toHaveCount(20)
}

test('loads 20 demo credentials in under 1 s (median of 5) with one listing request', async ({
  page,
}) => {
  const problems = await guard(page)
  const durations: number[] = []
  for (let i = 0; i < 5; i++) {
    const requests: string[] = []
    page.on('request', (r) => requests.push(new globalThis.URL(r.url()).pathname))
    await page.goto(i === 0 ? URL : BASE) // token kept in sessionStorage after the first load
    await expect(page.getByTestId('credential-card')).toHaveCount(20)
    await page.waitForFunction(
      () => performance.getEntriesByName('mecha:cards-rendered').length > 0,
    )
    durations.push(
      await page.evaluate(
        () => performance.getEntriesByName('mecha:cards-rendered')[0]?.startTime ?? 1e9,
      ),
    )
    expect(requests.filter((p) => p === '/api/credentials')).toHaveLength(1)
    expect(requests.filter((p) => p.includes('/history'))).toHaveLength(0) // no N+1
    page.removeAllListeners('request')
  }
  durations.sort((a, b) => a - b)
  const median = durations[2] as number
  console.log(
    `cards-rendered: ${durations.map((d) => d.toFixed(0)).join(', ')} ms (median ${median.toFixed(0)})`,
  )
  expect(median).toBeLessThan(1000)
  expect(problems).toEqual([])
})

test('demo badge, every state has text and an icon, not only colour', async ({ page }) => {
  const problems = await guard(page)
  await load(page)
  await expect(page.getByTestId('demo-badge')).toBeVisible()
  for (const card of await page.getByTestId('credential-card').all()) {
    const pill = card.locator('[data-status]')
    await expect(pill).toHaveText(/\S/)
    await expect(pill.locator('svg')).toHaveCount(1)
  }
  // The counters always add up to the total (five states, «local only» included).
  let total = 0
  for (const state of ['green', 'amber', 'red', 'grey', 'local']) {
    await expect(page.getByTestId(`counter-${state}`)).toBeVisible()
    const n = await page.locator(`[data-testid="credential-card"][data-overall="${state}"]`).count()
    expect(n).toBeGreaterThan(0)
    total += n
  }
  expect(total).toBe(20)
  expect(problems).toEqual([])
})

for (const theme of ['dark', 'light'] as const) {
  test(`no axe violations in the ${theme} theme`, async ({ page }) => {
    const problems = await guard(page)
    await page.addInitScript((t) => window.localStorage.setItem('mecha.theme', t), theme)
    await load(page)
    await expect(page.locator('html')).toHaveClass(new RegExp(theme))
    const results = await new AxeBuilder({ page }).analyze()
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([])
    expect(problems).toEqual([])
  })
}

test('fully usable with the keyboard', async ({ page }) => {
  const problems = await guard(page)
  await load(page)
  // N opens the add dialog (disabled in demo), Esc closes it.
  await page.keyboard.press('n')
  await expect(page.getByTestId('add-dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('add-dialog')).toBeHidden()
  // ⌘K / Ctrl+K palette: jump to a credential.
  await page.keyboard.press('ControlOrMeta+k')
  await expect(page.getByPlaceholder(/jump to a key|salta a una clave/i)).toBeFocused() // lazy-loaded
  await page.keyboard.type('Leaked')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('detail-sheet')).toBeVisible()
  await expect(page.getByTestId('detail-sheet')).toContainText('Leaked in a gist')
  await expect(page.getByTestId('detail-sheet')).toContainText(/history|historial/i)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('detail-sheet')).toBeHidden()
  // Tab reaches a card title button and Enter opens it.
  await page.keyboard.press('/')
  await expect(page.locator('#search')).toBeFocused()
  await page.keyboard.type('Grok')
  await expect(page.getByTestId('credential-card')).toHaveCount(1)
  const title = page
    .getByTestId('credential-card')
    .getByRole('button', { name: 'Grok team key', exact: true })
  await title.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('detail-sheet')).toBeVisible()
  expect(problems).toEqual([])
})

test('check all streams progress and refreshes the cards', async ({ page }) => {
  const problems = await guard(page)
  await load(page)
  await page.getByRole('button', { name: /check all|comprobar todo/i }).click()
  await expect(page.locator('#check-progress')).toHaveText('', { timeout: 10_000 })
  await expect(page.getByTestId('credential-card')).toHaveCount(20)
  expect(problems).toEqual([])
})

test.describe('Spanish browser', () => {
  test.use({ locale: 'es-ES' })

  test('the UI follows the browser language and sets <html lang>', async ({ page }) => {
    const problems = await guard(page)
    await load(page)
    await expect(page.locator('html')).toHaveAttribute('lang', 'es')
    await expect(page.getByRole('button', { name: 'Comprobar todo' })).toBeVisible()
    await expect(page.getByTestId('counter-red')).toContainText('Roto')
    await expect(page.locator('[data-status="local"]').first()).toHaveText('Solo local')
    await page.getByRole('button', { name: 'Leaked in a gist', exact: true }).click()
    await expect(page.getByTestId('detail-sheet')).toContainText('Bloqueada')
    expect(problems).toEqual([])
  })
})
