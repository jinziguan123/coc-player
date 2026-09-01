/**
 * 页面级组件的**行数红线**。
 *
 * 项目对几个大页面（GameSessionPage / CharacterPage / SettingsPage）的判断是：
 * 可测的时序与派生逻辑已经抽出（`lib/liveSession.ts`、`features/game-session/derive.ts`），
 * 剩余部分基本是 prop plumbing，硬拆的风险大于收益（DESIGN §13 已知边界 7）。
 *
 * 这个判断成立，但它此前只是一句话——**没有任何东西阻止这些文件继续长**。
 * 「等需要为它写测试时再动」在实践中等于永不动，因为文件越大越没人想给它写测试。
 *
 * 所以把那句话变成一道会失败的闸：现有大文件按当前行数就地封顶（不要求缩小，
 * 只要求**不再涨**），新文件一律受统一上限约束。
 *
 * 撞线时的正确反应**不是**把数字调大，而是把这次要加的东西放进一个新组件/新 hook。
 * 确有理由放宽时，改这里并在提交信息里说明为什么这次拆不动。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = path.resolve(__dirname, '../src')

/** 新文件的统一上限：超过这个长度就该拆了。 */
const DEFAULT_MAX = 700

/**
 * 存量大文件的就地封顶。数字 = 立此规矩时的实际行数，**只降不升**。
 * 每一条都欠着一次拆分；拆完请把数字调下来，别留着当额度用。
 */
const GRANDFATHERED: Record<string, number> = {
  'pages/GameSessionPage.tsx': 2480,
  'pages/CharacterPage.tsx': 1776,
  'components/game/HumanKpPanel.tsx': 1441,
  'pages/ModuleDetailPage.tsx': 1261,
  'pages/RoomLobbyPage.tsx': 1019,
  'pages/SettingsPage.tsx': 722,
}

/** 生成物不算数：它由 openapi-typescript 产出，长度不受我们控制。 */
const GENERATED = new Set(['api/generated.ts'])

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full)
  }
  return out
}

describe('源文件行数红线', () => {
  const files = walk(SRC).map((full) => {
    const rel = path.relative(SRC, full).split(path.sep).join('/')
    return { rel, lines: readFileSync(full, 'utf-8').split('\n').length }
  })

  it('没有文件突破自己的上限', () => {
    const over = files
      .filter(({ rel }) => !GENERATED.has(rel))
      .map(({ rel, lines }) => ({ rel, lines, max: GRANDFATHERED[rel] ?? DEFAULT_MAX }))
      .filter(({ lines, max }) => lines > max)
      .map(({ rel, lines, max }) => `${rel}: ${lines} 行 > 上限 ${max}`)

    expect(
      over,
      `这些文件超出行数上限。正确做法是把新增内容放进新组件/新 hook，而不是调大数字：\n${over.join('\n')}`,
    ).toEqual([])
  })

  it('封顶清单里没有已经不存在或已经缩小很多的条目', () => {
    // 文件被删/被拆小以后，额度要跟着收回，否则它会悄悄变成下一次膨胀的空间。
    const byRel = new Map(files.map((f) => [f.rel, f.lines]))
    const stale = Object.entries(GRANDFATHERED)
      .filter(([rel, max]) => {
        const lines = byRel.get(rel)
        return lines === undefined || lines < max - 200
      })
      .map(([rel, max]) => `${rel}: 上限 ${max}，实际 ${byRel.get(rel) ?? '文件已不存在'}`)

    expect(stale, `封顶清单该更新了（把上限调到当前行数，或删掉该条）：\n${stale.join('\n')}`)
      .toEqual([])
  })
})
