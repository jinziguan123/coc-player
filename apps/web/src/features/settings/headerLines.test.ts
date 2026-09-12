import { describe, expect, it } from 'vitest'
import { formatHeaderLines, parseHeaderLines } from './headerLines'

describe('自定义请求头的文本与对象互转', () => {
  it('每行「名称: 值」解析成对象，值里的冒号保留，空行与坏行跳过', () => {
    expect(parseHeaderLines('x-goog-api-key: AIza1\n\nHTTP-Referer: https://a.b/c\n没有冒号的行\n: 只有值\n名称: ')).toEqual({
      'x-goog-api-key': 'AIza1',
      'HTTP-Referer': 'https://a.b/c',
    })
  })
  it('全角冒号也认', () => {
    expect(parseHeaderLines('x-tenant：t1')).toEqual({ 'x-tenant': 't1' })
  })
  it('对象回填成文本，再解析回去不变', () => {
    const obj = { 'x-a': '1', 'x-b': 'https://x:8080/p' }
    expect(parseHeaderLines(formatHeaderLines(obj))).toEqual(obj)
    expect(formatHeaderLines(undefined)).toBe('')
  })
})
