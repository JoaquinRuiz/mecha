import { apiFetch } from './session'
import type {
  CheckResult,
  CredentialStatus,
  Detection,
  EnvEntry,
  LinkCandidate,
  Listing,
  ProviderInfo,
} from './types'

export interface NewCredential {
  secret: string
  label?: string
  tags?: string[]
  provider?: string
  kind?: string
  manual_expires_at?: string | null
}

export interface CredentialPatch {
  label?: string
  tags?: string[]
  notes?: string | null
  manual_expires_at?: string | null
  active_probe?: boolean
  manual_budget?: { amount: string; currency: string; purchased_at: string } | null
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
})

export function makeApi(token: string | null) {
  return {
    health: () => apiFetch<{ status: 'ok'; version: string; demo: boolean }>(token, '/health'),
    list: () => apiFetch<Listing>(token, '/credentials'),
    providers: () => apiFetch<{ providers: ProviderInfo[] }>(token, '/providers'),
    history: (id: string, limit = 50) =>
      apiFetch<{ history: CheckResult[] }>(
        token,
        `/credentials/${encodeURIComponent(id)}/history?limit=${limit}`,
      ),
    detect: (secret: string, signal?: AbortSignal) =>
      apiFetch<Detection>(token, '/detect', { ...json({ secret }), signal }),
    add: (body: NewCredential) => apiFetch<CredentialStatus>(token, '/credentials', json(body)),
    patch: (id: string, body: CredentialPatch) =>
      apiFetch<CredentialStatus>(token, `/credentials/${encodeURIComponent(id)}`, {
        ...json(body),
        method: 'PATCH',
      }),
    remove: (id: string) =>
      apiFetch<null>(token, `/credentials/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    check: (id: string, force = true) =>
      apiFetch<CredentialStatus>(
        token,
        `/credentials/${encodeURIComponent(id)}/check?force=${force}`,
        {
          method: 'POST',
        },
      ),
    checkAll: () =>
      apiFetch<{ started: boolean; total: number }>(token, '/check-all', { method: 'POST' }),
    /** Paid active probe; 409 probe_too_soon unless `force` (after explicit confirmation). */
    probe: (id: string, force = false) =>
      apiFetch<CredentialStatus>(
        token,
        `/credentials/${encodeURIComponent(id)}/check?mode=active&force=${force}`,
        { method: 'POST' },
      ),
    envPreview: (content: string) =>
      apiFetch<{ entries: EnvEntry[] }>(token, '/import/env/preview', json({ content })),
    envImport: (content: string, select: number[], providers: Record<number, string> = {}) =>
      apiFetch<{
        results: { index: number; ok: boolean; id?: string; code?: string; message?: string }[]
      }>(token, '/import/env', json({ content, select, providers })),
    linkCandidates: () => apiFetch<{ candidates: LinkCandidate[] }>(token, '/linked/candidates'),
    link: (tool: string, path?: string, label?: string) =>
      apiFetch<CredentialStatus>(
        token,
        '/linked',
        json({ tool, path: path || null, label: label || null }),
      ),
  }
}

export type Api = ReturnType<typeof makeApi>
