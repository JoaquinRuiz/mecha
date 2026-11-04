import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ApiError, apiFetch, initSessionToken } from '@/lib/session'

type Health = { status: 'ok'; version: string }

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; health: Health }
  | { kind: 'locked' }
  | { kind: 'error'; message: string }

export function App({ token = initSessionToken() }: { token?: string | null }) {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [attempt, setAttempt] = useState(0)

  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` re-runs the check on retry.
  useEffect(() => {
    if (!token) {
      setState({ kind: 'locked' })
      return
    }
    let cancelled = false
    apiFetch<Health>(token, '/health')
      .then((health) => !cancelled && setState({ kind: 'ready', health }))
      .catch((e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 401) setState({ kind: 'locked' })
        else setState({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      cancelled = true
    }
  }, [token, attempt])

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 p-6">
      <h1 className="font-semibold text-2xl tracking-tight">Mecha</h1>
      {state.kind === 'loading' && <p className="text-muted-foreground">Connecting…</p>}
      {state.kind === 'ready' && (
        <p role="status">Connected to the local server · v{state.health.version}</p>
      )}
      {state.kind === 'locked' && (
        <div role="alert" className="space-y-2">
          <p className="font-medium">Locked</p>
          <p className="text-muted-foreground text-sm">
            This page needs the session link printed by <code>mecha serve</code>. Open that link
            again from your terminal.
          </p>
        </div>
      )}
      {state.kind === 'error' && (
        <div role="alert" className="space-y-2">
          <p className="font-medium">Cannot reach the local server</p>
          <p className="text-muted-foreground text-sm">{state.message}</p>
          <Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>
            Retry
          </Button>
        </div>
      )}
    </main>
  )
}
