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
    return md.render(content)
  }

  return { render }
}
