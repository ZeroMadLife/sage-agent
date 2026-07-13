import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: true,
  highlight(code: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="hljs"><code>${hljs.highlight(code, { language: lang }).value}</code></pre>`
      } catch {
        // fall through to default
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(code)}</code></pre>`
  },
})

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
    return md.render(content)
  }

  return { render }
}
