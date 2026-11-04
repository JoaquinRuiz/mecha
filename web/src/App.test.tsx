import { render, screen } from '@testing-library/react'
import { App } from './App'

function mockFetch(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(body), { status }))
}

describe('App', () => {
  it('shows connected when the token is accepted', async () => {
    const fetchMock = mockFetch(200, { status: 'ok', version: '0.1.0' })
    render(<App token="tok" />)
    expect(await screen.findByRole('status')).toHaveTextContent('v0.1.0')
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer tok')
  })

  it('shows locked on 401', async () => {
    mockFetch(401, { code: 'unauthorized', message: 'nope' })
    render(<App token="bad" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Locked')
  })

  it('shows locked without a token and does not call the API', async () => {
    const fetchMock = mockFetch(200, {})
    render(<App token={null} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Locked')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
