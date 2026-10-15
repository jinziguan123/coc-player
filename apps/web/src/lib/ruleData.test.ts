import { describe, expect, it } from 'vitest'
import MANIFEST from '@/components/game/__fixtures__/coc-manifest.json'
import { blankRow, cellText, listColumns, localStatus, matchesQuery, tableFields, type RuleTableManifest } from './ruleData'

const table = (name: string) => MANIFEST.tables.find((t) => t.name === name) as unknown as RuleTableManifest

describe('数据表 schema 解读', () => {
  it('武器表：字段、类型、区间都来自后端 schema', () => {
    const fields = tableFields(table('weapons'))
    expect(fields.map((f) => f.key)).toEqual([
      'name', 'skill', 'dam', 'tho', 'range', 'round', 'num', 'price', 'err', 'time', 'category',
    ])
    const tho = fields.find((f) => f.key === 'tho')!
    expect(tho).toMatchObject({ kind: 'integer', min: 0, max: 1, title: '贯穿' })
    expect(fields.find((f) => f.key === 'name')!.required).toBe(true)
  })

  it('职业表：本职技能是字符串列表', () => {
    expect(tableFields(table('occupations')).find((f) => f.key === 'skills')!.kind).toBe('string_list')
  })

  it('专精表：专精列表是对象列表，子字段取自 $defs', () => {
    const items = tableFields(table('specializations')).find((f) => f.key === 'items')!
    expect(items.kind).toBe('object_list')
    expect(items.itemFields!.map((f) => f.key)).toEqual(['name', 'init'])
  })

  it('疯狂症状：带 x-choices 的字段是下拉', () => {
    const override = tableFields(table('madness_symptoms')).find((f) => f.key === 'override')!
    expect(override.kind).toBe('choice')
    expect(cellText(override, 'flee')).toBe('逃跑')
  })

  it('新增一行取 schema 默认值', () => {
    const row = blankRow(tableFields(table('weapons')))
    expect(row).toMatchObject({ name: '', tho: 0, round: '1', category: '常规' })
  })

  it('列表列：主键在前，长列表字段不进列', () => {
    const cols = listColumns(tableFields(table('occupations')), 'name')
    expect(cols[0].key).toBe('name')
    expect(cols.map((c) => c.key)).not.toContain('skills')
    expect(cols.length).toBeLessThanOrEqual(4)
  })

  it('本地改过的官方行记「已修改」，服务端没有的行是「新增」', () => {
    const server = { 弓箭: 'official', 斧头: 'modified' }
    expect(localStatus('弓箭', server, new Set(['弓箭']))).toBe('modified')
    expect(localStatus('弓箭', server, new Set())).toBe('official')
    expect(localStatus('雷霆之锤', server, new Set())).toBe('added')
  })

  it('搜索命中任一字段', () => {
    expect(matchesQuery({ name: '弓箭', skill: '射击(弓弩)' }, '弓弩')).toBe(true)
    expect(matchesQuery({ name: '弓箭' }, '手枪')).toBe(false)
  })
})
