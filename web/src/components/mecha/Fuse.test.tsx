import { screen } from '@testing-library/react'
import { makeI18n } from '@/i18n'
import { NOW, status } from '@/test/fixtures'
import { renderUi } from '@/test/render'
import { Fuse, fuseModel } from './Fuse'

const i18n = makeI18n('en')
const model = (expires_at: string | null) =>
  fuseModel(status({ expires_at }), NOW, i18n.t, i18n.relative)

describe('fuseModel', () => {
  it('burns down towards expiry, with the traffic-light thresholds', () => {
    expect(model('2026-12-31T00:00:00Z').tone).toBe('green')
    expect(model('2026-11-10T00:00:00Z').tone).toBe('amber') // < 14 days
    const gone = model('2026-11-01T00:00:00Z')
    expect(gone.tone).toBe('red')
    expect(gone.fraction).toBe(0)
    expect(gone.text).toMatch(/^Expired/)
  })

  it('without a known date it says so', () => {
    const m = model(null)
    expect(m.fraction).toBeNull()
    expect(m.text).toBe('No known date')
  })
})

describe('Fuse', () => {
  it('is an accessible meter with an animated spark', () => {
    renderUi(<Fuse model={model('2026-12-31T00:00:00Z')} />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('value')
    expect(meter).toHaveAccessibleName(/Expires/)
    expect(screen.getByTestId('fuse-spark')).toBeInTheDocument()
  })

  it('respects prefers-reduced-motion: no spark', () => {
    renderUi(<Fuse model={model('2026-12-31T00:00:00Z')} />, { reducedMotion: 'always' })
    expect(screen.getByRole('meter')).toBeInTheDocument()
    expect(screen.queryByTestId('fuse-spark')).toBeNull()
  })

  it('unknown date is a dotted bar with text, not just a colour', () => {
    renderUi(<Fuse model={model(null)} />)
    expect(screen.getByRole('img', { name: 'No known date' })).toBeInTheDocument()
    expect(screen.getByText('No known date')).toBeInTheDocument()
  })
})
