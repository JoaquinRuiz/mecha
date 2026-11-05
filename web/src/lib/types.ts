// Shapes returned by the local API (see src/mecha/core/serialize.py). No secrets, ever.

export type Overall = 'green' | 'amber' | 'red' | 'grey' | 'local'
export type Source = 'api' | 'header' | 'jwt' | 'file' | 'manual' | 'inferred'
export type ProviderId = 'anthropic' | 'openai' | 'gemini' | 'xai' | 'openrouter'
export type Kind = 'api_key' | 'admin_key' | 'management_key' | 'oauth_token' | 'oauth_file'

export interface Signal<T> {
  value: T
  source: Source
  observed_at: string
}

export interface Credential {
  id: string
  label: string
  provider: ProviderId
  kind: Kind
  redacted: string
  fingerprint: string
  tags: string[]
  notes: string | null
  linked_path: string | null
  manual_expires_at: string | null
  created_at: string
  token_expires_at: string | null
  subscription_until: string | null
  retiring: boolean
}

export interface RateWindow {
  limit: number | null
  remaining: number | null
  reset: string | null
}

export interface CheckResult {
  credential_id: string
  checked_at: string
  mode: 'offline' | 'passive' | 'active' | 'enrich'
  check_status: 'not_checked' | 'failed' | 'ok'
  overall: Overall
  reasons: { code: string; overall: Overall }[]
  validity: Signal<string>
  funds: Signal<{ state: string; amount: string | null; currency: string | null }>
  expiry: Signal<{ state: string; expires_at: string | null }>
  limits: Signal<{ state: string; requests: RateWindow | null; tokens: RateWindow | null }> | null
  account: Record<string, Signal<string>>
  http: {
    endpoint: string
    status: number | null
    latency_ms: number | null
    headers: Record<string, string>
  } | null
  error: { code: string; message: string } | null
}

export interface CredentialStatus {
  credential: Credential
  result: CheckResult
  cached: boolean
  last_known: { overall: Overall; checked_at: string } | null
}

export interface Listing {
  schema_version: number
  generated_at: string
  demo: boolean
  credentials: CredentialStatus[]
}

export interface Detection {
  format: 'prefix' | 'regex' | 'jwt' | 'session_file' | 'unknown'
  provider: ProviderId | null
  kind: Kind | null
  label: string | null
  verified: boolean
  ambiguous: boolean
  candidates: ProviderId[]
  storable: boolean
  retiring: boolean
  redacted: string
  warnings: string[]
}

export type Capability = 'yes' | 'no' | 'error_only'

export interface ProviderInfo {
  id: ProviderId
  name: string
  validate: Capability
  funds: Capability
  expiry: Capability
  kinds: Kind[]
}

export const OVERALL_ORDER: Overall[] = ['red', 'amber', 'grey', 'local', 'green']
