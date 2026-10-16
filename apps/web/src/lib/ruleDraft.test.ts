import { describe, expect, it } from 'vitest'
import MANIFEST from '@/components/game/__fixtures__/coc-manifest.json'
import type { RuleSystemManifest } from './ruleSchema'
import {
  buildSections, deletedRows, diffFromRaw, officialRows, optionChanges, optionIndex, rawValues,
  rowStatus, tableChanges, tableDiffCount,
} from './ruleDraft'

const M = MANIFEST as unknown as RuleSystemManifest
const RAW = rawValues(M)
const index = optionIndex(M)

describe('规则页分区', () => {
  it('规则分区来自组件 group，按预设顺序；前后固定有概览、数据表、约定、插件', () => {
    expect(buildSections(M).map((s) => s.title)).toEqual([
      '概览', '检定', '幸运', '伤害与状态', '理智与疯狂', '成长', '建卡', '数据表', '桌面约定', '插件',
    ])
  })

  it('原文值取自 schema 默认值', () => {
    expect(RAW).toMatchObject({ critical_max: 1, fumble_rule: 'raw', luck_spend: false, improvement: true })
  })
})

describe('与原文的差异', () => {
  it('可选规则关着时，它的参数不算差异', () => {
    expect(diffFromRaw(index, { ...RAW, luck_spend_max: 9 })).toEqual([])
    const on = diffFromRaw(index, { ...RAW, luck_spend: true, luck_spend_max: 9 }).map((d) => d.key)
    expect(on).toEqual(['luck_spend', 'luck_spend_max'])
  })

  it('show_if 不满足的参数不算差异', () => {
    expect(diffFromRaw(index, { ...RAW, insanity_flat_threshold: 8 })).toEqual([])
  })
})

describe('草稿相对已保存的改动', () => {
  it('写成人话：参数给前后值，开关给开/关', () => {
    const texts = optionChanges(index, RAW, { ...RAW, fumble_rule: 'hundred_only', bleeding: true }).map((c) => c.text)
    expect(texts).toEqual(['大失败阈值 照规则书→只认 100', '流血 开'])
  })

  it('每条改动带着所属分区', () => {
    const c = optionChanges(index, RAW, { ...RAW, critical_max: 3 })
    expect(c[0].section).toBe('rules:检定')
  })
})

describe('数据表行的状态', () => {
  const view = {
    rows: [{ name: '弓箭', dam: '1D8' }, { name: '斧头', dam: '1D8+DB' }, { name: '雷霆之锤', dam: '2D8' }],
    status: { 弓箭: 'modified', 斧头: 'official', 雷霆之锤: 'added' },
    deleted: [{ name: '长鞭', dam: '1D3' }],
    originals: { 弓箭: { name: '弓箭', dam: '1D6' } },
  }
  const official = officialRows(view, 'name')

  it('官方原样 = 非新增行（改过的取原样）+ 被删的官方行', () => {
    expect(official).toEqual([{ name: '弓箭', dam: '1D6' }, { name: '斧头', dam: '1D8+DB' }, { name: '长鞭', dam: '1D3' }])
  })

  it('状态由官方数据与当前行现算', () => {
    expect(rowStatus(official, view.rows[0], 'name')).toBe('modified')
    expect(rowStatus(official, view.rows[1], 'name')).toBe('official')
    expect(rowStatus(official, view.rows[2], 'name')).toBe('added')
    expect(deletedRows(official, view.rows, 'name')).toEqual([{ name: '长鞭', dam: '1D3' }])
    expect(tableDiffCount(official, view.rows, 'name')).toBe(3)
  })

  it('草稿表相对已保存表的改动', () => {
    const draft = [{ name: '弓箭', dam: '1D10' }, { name: '雷霆之锤', dam: '2D8' }, { name: '匕首', dam: '1D4' }]
    expect(tableChanges('weapons', '武器表', view.rows, draft, 'name').map((c) => c.text)).toEqual([
      '武器表 改弓箭', '武器表 +匕首', '武器表 删斧头',
    ])
  })
})
