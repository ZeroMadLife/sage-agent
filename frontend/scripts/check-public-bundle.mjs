import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
const dist = join(root, '../dist-public')

// Match private app routes / auth markers, not public asset names like assistant-desktop.webp.
const forbiddenPatterns = [
  { token: 'route:/assistant', re: /["'`]\/assistant(?:\/|["'`?#]|$)/g },
  { token: 'route:/coding/session', re: /["'`]\/coding\/session(?:\/|["'`?#]|$)/g },
  { token: 'route:/settings/', re: /["'`]\/settings\/[A-Za-z0-9_-]+/g },
  { token: 'CloudAuthGate', re: /CloudAuthGate/g },
  { token: 'useCloudAuth', re: /useCloudAuth/g },
  { token: 'VITE_CLOUD_AUTH_REQUIRED', re: /VITE_CLOUD_AUTH_REQUIRED/g },
]

function walk(dir) {
  const entries = readdirSync(dir)
  const files = []
  for (const entry of entries) {
    const full = join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) files.push(...walk(full))
    else if (/\.(js|css|html|map)$/.test(entry)) files.push(full)
  }
  return files
}

let files
try {
  files = walk(dist)
} catch {
  console.error(`check-public-bundle: missing ${dist}. Run build:public first.`)
  process.exit(1)
}

const hits = []
for (const file of files) {
  const text = readFileSync(file, 'utf8')
  for (const pattern of forbiddenPatterns) {
    if (pattern.re.test(text)) hits.push({ file, token: pattern.token })
    pattern.re.lastIndex = 0
  }
}

if (hits.length) {
  console.error('check-public-bundle: private application markers found in public bundle:')
  for (const hit of hits) console.error(`- ${hit.token} in ${hit.file}`)
  process.exit(1)
}

console.log(`check-public-bundle: ok (${files.length} files scanned)`)
