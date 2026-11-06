import { type ChildProcess, spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Starts the demo server and reads the session URL it prints (no extra env var needed).
const PORT = 7899

export default async function globalSetup(): Promise<() => Promise<void>> {
  const repo = resolve(import.meta.dirname, '..', '..')
  const home = mkdtempSync(join(tmpdir(), 'mecha-e2e-'))
  const child: ChildProcess = spawn(
    'uv',
    ['run', 'mecha', 'serve', '--demo', '--no-open', '--port', String(PORT)],
    {
      cwd: repo,
      env: { ...process.env, MECHA_HOME: home },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  const url = await new Promise<string>((ok, fail) => {
    let out = ''
    const timer = setTimeout(() => fail(new Error(`server did not start:\n${out}`)), 60_000)
    const onData = (chunk: Buffer) => {
      out += chunk.toString()
      const m = out.match(/Open: (http:\/\/\S+)/)
      if (m && out.includes('Uvicorn running')) {
        clearTimeout(timer)
        ok(m[1] as string)
      }
    }
    child.stdout?.on('data', onData)
    child.stderr?.on('data', onData)
    child.on('exit', (code) => fail(new Error(`server exited (${code}):\n${out}`)))
  })
  writeFileSync(join(import.meta.dirname, '.session-url'), url)
  return async () => {
    child.kill('SIGINT')
    await new Promise((r) => child.once('exit', r))
  }
}
