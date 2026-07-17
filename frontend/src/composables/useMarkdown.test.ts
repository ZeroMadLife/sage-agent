import { expect, it } from 'vitest'
import { useMarkdown } from './useMarkdown'

it('does not turn repository filenames into public links', () => {
  const { render } = useMarkdown()

  expect(render('读取 README.md')).not.toContain('<a ')
})

it('keeps protocol and explicit Markdown links safe', () => {
  const { render } = useMarkdown()

  const protocolLink = render('访问 https://example.com')
  const explicitLink = render('[文档](https://example.com/docs)')

  expect(protocolLink).toContain('href="https://example.com"')
  expect(protocolLink).toContain('target="_blank"')
  expect(protocolLink).toContain('rel="noopener noreferrer"')
  expect(explicitLink).toContain('href="https://example.com/docs"')
})

it('renders fenced code with a language header and copy affordance', () => {
  const { render } = useMarkdown()

  const output = render('```json\n{"ok": true}\n```')

  expect(output).toContain('class="sage-code-block"')
  expect(output).toContain('data-language="json"')
  expect(output).toContain('data-copy-code')
  expect(output).toContain('aria-label="复制代码"')
  expect(output).not.toContain('<pre><code class="language-json"><div')
})
