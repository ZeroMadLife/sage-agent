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

it('repairs compact headings and table rows from streamed assistant output', () => {
  const { render } = useMarkdown()

  const output = render(
    '##能力检查结果|能力状态||------|------||**Research 子代理**|不可用',
  )

  expect(output).toContain('<h2>能力检查结果</h2>')
  expect(output).toContain('<table>')
  expect(output).toContain('Research 子代理')
  expect(output).toContain('不可用')
})

it('hides legacy tool protocol from stored assistant messages', () => {
  const { render } = useMarkdown()

  const output = render(
    '准备检查。<tool>{"name":"run_shell","args":{"command":"pwd"}}</tool>'
      + '<final>检查完成。</final>',
  )

  expect(output).toContain('准备检查。')
  expect(output).toContain('检查完成。')
  expect(output).not.toContain('&lt;tool&gt;')
  expect(output).not.toContain('run_shell')
  expect(output).not.toContain('&lt;final&gt;')
})
