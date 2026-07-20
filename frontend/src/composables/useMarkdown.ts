import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
})

md.renderer.rules.fence = function (tokens, idx) {
  const token = tokens[idx]
  const requestedLanguage = token.info.trim().split(/\s+/)[0]
  const language = requestedLanguage && hljs.getLanguage(requestedLanguage) ? requestedLanguage : 'text'
  const escapedLanguage = md.utils.escapeHtml(language)
  let highlighted = md.utils.escapeHtml(token.content)
  if (language !== 'text') {
    try {
      highlighted = hljs.highlight(token.content, { language }).value
    } catch {
      highlighted = md.utils.escapeHtml(token.content)
    }
  }
  return `<div class="sage-code-block" data-language="${escapedLanguage}"><div class="code-block-header"><span>${escapedLanguage}</span><button type="button" class="code-copy-button" data-copy-code aria-label="复制代码">复制</button></div><pre class="hljs"><code>${highlighted}</code></pre></div>\n`
}

// Keep protocol URLs linkable without treating repository names such as
// README.md as public domains.
md.linkify.set({ fuzzyLink: false })

// 外部链接加 target=_blank 和 rel=noopener
const defaultRender =
  md.renderer.rules.link_open ||
  function (tokens, idx, options, _env, self) {
    return self.renderToken(tokens, idx, options)
  }

md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const targetIndex = tokens[idx].attrIndex('target')
  if (targetIndex < 0) {
    tokens[idx].attrPush(['target', '_blank'])
    tokens[idx].attrPush(['rel', 'noopener noreferrer'])
  } else {
    tokens[idx].attrs![targetIndex][1] = '_blank'
  }
  return defaultRender(tokens, idx, options, env, self)
}

export function useMarkdown() {
  function render(content: string): string {
    if (!content) return ''
    return md.render(normalizeCompactMarkdown(content))
  }

  return { render }
}

function normalizeCompactMarkdown(content: string): string {
  return content
    .split(/(```[\s\S]*?```)/g)
    .map((segment, index) => index % 2 === 1 ? segment : normalizeMarkdownText(segment))
    .join('')
}

function normalizeMarkdownText(content: string): string {
  let normalized = content
    .replace(/<tool>\s*\{[\s\S]*?\}\s*<\/tool>/gi, '')
    .replace(/<\/?final>/gi, '')
    .replace(/\r\n?/g, '\n')
  normalized = normalized.replace(/([^#\n])(?=#{1,6})/g, '$1\n\n')
  normalized = normalized.replace(/^(#{1,6})(?=\S)/gm, '$1 ')

  // Some providers collapse table row separators while streaming. Repair only
  // strings that clearly contain a compact Markdown table, leaving prose `||`
  // untouched.
  const compactTable = /\|[^|\n]+\|[^|\n]+\|\|/.test(normalized) && /\|\s*:?-{3,}/.test(normalized)
  if (compactTable) {
    normalized = normalized.replace(/^(#{1,6})\s+([^|\n]+)\|/m, '$1 $2\n\n|')
    normalized = normalized.replace(/([^\n])(?=\|[^|\n]+\|[^|\n]+\|\|)/g, '$1\n')
    normalized = normalized.replace(/\|\|/g, '|\n|')
    normalized = normalized.replace(
      /(\n\n)\|([^|\n]+)\|\n\|(-{3,})\|(-{3,})\|/,
      '$1| |$2|\n|$3|$4|',
    )
  }
  return normalized
}
