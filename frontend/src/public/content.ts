import siteMetaJson from '../../public-content/site.meta.json'
import homeSectionsJson from '../../public-content/home.sections.json'
import askCorpusJson from '../../public-content/ask-corpus.json'
import whyDurableTimeline from '../../public-content/notes/why-durable-timeline.md?raw'
import approvalIsNotDecoration from '../../public-content/notes/approval-is-not-decoration.md?raw'
import publicSiteIsReadOnly from '../../public-content/notes/public-site-is-read-only.md?raw'

export type SiteMeta = {
  brand: string
  title: string
  description: string
  githubRepoUrl: string
  githubRepoApi: string
}

export type HomeSections = typeof homeSectionsJson

export type PublicNoteRelated = {
  label: string
  href: string
}

export type PublicNote = {
  slug: string
  title: string
  date: string
  summary: string
  tags: string[]
  related: PublicNoteRelated[]
  body: string
}

export type AskCorpusSource = {
  id: string
  label: string
  target: string
  detail: string
}

export type AskCorpusEntry = {
  keywords: string[]
  answer: string
  sources: AskCorpusSource[]
}

export type AskCorpus = {
  entries: AskCorpusEntry[]
  fallback: string
}

const noteSources: Record<string, string> = {
  'why-durable-timeline': whyDurableTimeline,
  'approval-is-not-decoration': approvalIsNotDecoration,
  'public-site-is-read-only': publicSiteIsReadOnly,
}

function parseScalar(value: string): string {
  const trimmed = value.trim()
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"'))
    || (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function parseTags(value: string): string[] {
  const trimmed = value.trim()
  if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
    return trimmed
      .slice(1, -1)
      .split(',')
      .map((item) => parseScalar(item))
      .filter(Boolean)
  }
  return []
}

export function parseNoteMarkdown(raw: string, slug = 'note'): PublicNote {
  const normalized = raw.replace(/\r\n/g, '\n')
  if (!normalized.startsWith('---\n')) {
    return {
      slug,
      title: slug,
      date: '',
      summary: '',
      tags: [],
      related: [],
      body: normalized.trim(),
    }
  }

  const end = normalized.indexOf('\n---\n', 4)
  if (end < 0) {
    return {
      slug,
      title: slug,
      date: '',
      summary: '',
      tags: [],
      related: [],
      body: normalized.trim(),
    }
  }

  const frontmatter = normalized.slice(4, end)
  const body = normalized.slice(end + 5).trim()
  const fields: Record<string, string> = {}
  const related: PublicNoteRelated[] = []
  let inRelated = false
  let pendingRelated: Partial<PublicNoteRelated> = {}

  for (const line of frontmatter.split('\n')) {
    if (line.startsWith('related:')) {
      inRelated = true
      continue
    }
    if (inRelated) {
      const relatedItem = line.match(/^\s*-\s+label:\s*(.+)\s*$/)
      if (relatedItem) {
        if (pendingRelated.label && pendingRelated.href) {
          related.push(pendingRelated as PublicNoteRelated)
        }
        pendingRelated = { label: parseScalar(relatedItem[1]) }
        continue
      }
      const relatedHref = line.match(/^\s+href:\s*(.+)\s*$/)
      if (relatedHref) {
        pendingRelated.href = parseScalar(relatedHref[1])
        continue
      }
      if (!/^\s/.test(line) && line.includes(':')) {
        if (pendingRelated.label && pendingRelated.href) {
          related.push(pendingRelated as PublicNoteRelated)
        }
        pendingRelated = {}
        inRelated = false
      } else {
        continue
      }
    }

    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/)
    if (!match) continue
    fields[match[1]] = match[2]
  }

  if (pendingRelated.label && pendingRelated.href) {
    related.push(pendingRelated as PublicNoteRelated)
  }

  return {
    slug,
    title: parseScalar(fields.title || slug),
    date: parseScalar(fields.date || ''),
    summary: parseScalar(fields.summary || ''),
    tags: parseTags(fields.tags || ''),
    related,
    body,
  }
}

export function getSiteMeta(): SiteMeta {
  return siteMetaJson as SiteMeta
}

export function getHomeSections(): HomeSections {
  return homeSectionsJson
}

export function getAskCorpus(): AskCorpus {
  return askCorpusJson as AskCorpus
}

export function listNotes(): PublicNote[] {
  return Object.entries(noteSources)
    .map(([slug, raw]) => parseNoteMarkdown(raw, slug))
    .sort((left, right) => right.date.localeCompare(left.date))
}

export function getNoteBySlug(slug: string): PublicNote | null {
  return listNotes().find((note) => note.slug === slug) ?? null
}
