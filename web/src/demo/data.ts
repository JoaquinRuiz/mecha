import type { CheckResult, CredentialStatus, Kind, Listing, ProviderInfo } from '@/lib/types'
import raw from './data.json'
import { offsetFor, shiftDates } from './dates'

/** An API answer recorded from `mecha serve --demo` by scripts/dump_demo.py. */
export interface Answer {
  status: number
  body: unknown
}

export interface Sample {
  id: string
  label: string
  provider: string
  kind: Kind
  redacted: string
  added: CredentialStatus
  check: Answer
  probe: Answer
}

export interface DemoData {
  generated_at: string
  listing: Listing
  providers: { providers: ProviderInfo[] }
  history: Record<string, CheckResult[]>
  check: Record<string, Answer>
  probe: Record<string, Answer>
  samples: Sample[]
}

export const RAW = raw as unknown as DemoData

/** The dump with all its dates moved to "now" (one offset for every field). */
export function loadDemoData(now: number = Date.now()): DemoData {
  return shiftDates(RAW, offsetFor(RAW.generated_at, now))
}
