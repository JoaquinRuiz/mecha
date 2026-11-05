// Bundle size budget for the compiled frontend (src/mecha/static), gzip sizes.
// "initial" is what the first paint needs: the entry script plus its modulepreloads.
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'

const BUDGET_KB = { initialJs: 230, totalJs: 420, css: 25 }
const dir = join(import.meta.dirname, '..', '..', 'src', 'mecha', 'static')
const html = readFileSync(join(dir, 'index.html'), 'utf8')
const gz = (file) => gzipSync(readFileSync(join(dir, file))).length / 1024
const assets = readdirSync(join(dir, 'assets'))
const initialFiles = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.js)"/g)].map((m) => m[1])
const sizes = {
  initialJs: initialFiles.reduce((n, f) => n + gz(f), 0),
  totalJs: assets.filter((f) => f.endsWith('.js')).reduce((n, f) => n + gz(`assets/${f}`), 0),
  css: assets.filter((f) => f.endsWith('.css')).reduce((n, f) => n + gz(`assets/${f}`), 0),
}
let ok = true
for (const [key, kb] of Object.entries(sizes)) {
  const limit = BUDGET_KB[key]
  const line = `${key.padEnd(10)} ${kb.toFixed(1).padStart(7)} KB gzip (budget ${limit} KB)`
  if (kb > limit) {
    ok = false
    console.error(`over budget: ${line}`)
  } else {
    console.log(`ok: ${line}`)
  }
}
if (!initialFiles.length) {
  console.error('no entry script found in index.html')
  ok = false
}
process.exit(ok ? 0 : 1)
