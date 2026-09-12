/**
 * 战棋剪影目录与选择的存放。
 *
 * 为什么需要它：`Combatant` 上只有 name / side / hp，没有任何「这是人还是东西」的信息。
 * 按阵营一刀切的话，「不明存在」和「陌生男性」会共用同一个人形轮廓——而这两者在桌上
 * 是完全不同的两回事，KP 一眼扫过去必须能分清。这种事机器猜不了，该由 KP 点一下决定。
 *
 * 存哪儿：原型期存 localStorage，按「存档 + 参战方」作键。**正式版不该存这儿**——
 * 轮廓是桌面状态，要跟着战斗广播给所有玩家看，该落在 combat_state 的参战方投影里
 * （或 NPC 资料上，这样同一个 NPC 下次出场还是那个样子）。
 */
import {
  GiBat, GiBrainTentacle, GiCowled, GiCrocJaws, GiCultist, GiDeathSkull,
  GiDoctorFace, GiFedora, GiFishMonster, GiGhost, GiHoodedFigure, GiMonsterGrasp,
  GiOctopus, GiPerson, GiPoliceOfficerHead, GiShamblingZombie, GiSpectre,
  GiSpiderAlt, GiSuckeredTentacle, GiSwordman, GiTentacleStrike, GiWolfHead,
} from 'react-icons/gi'
import type { ComponentType } from 'react'

export type FigureKey = string
type IconType = ComponentType<{ size?: number | string }>

export interface FigureGroup {
  label: string
  items: { key: FigureKey; label: string; Icon: IconType }[]
}

/** 分组按「KP 在桌上怎么称呼它」来分，不按图标库的目录来分。 */
export const FIGURE_GROUPS: FigureGroup[] = [
  {
    label: '常人',
    items: [
      { key: 'person', label: '路人', Icon: GiPerson },
      { key: 'fedora', label: '调查员', Icon: GiFedora },
      { key: 'officer', label: '警察', Icon: GiPoliceOfficerHead },
      { key: 'doctor', label: '医生', Icon: GiDoctorFace },
      { key: 'swordman', label: '持械者', Icon: GiSwordman },
    ],
  },
  {
    label: '邪教',
    items: [
      { key: 'cultist', label: '信徒', Icon: GiCultist },
      { key: 'hooded', label: '兜帽人', Icon: GiHoodedFigure },
      { key: 'cowled', label: '祭司', Icon: GiCowled },
    ],
  },
  {
    label: '异类',
    items: [
      { key: 'tentacle', label: '触手', Icon: GiSuckeredTentacle },
      { key: 'tentacle2', label: '触腕', Icon: GiTentacleStrike },
      { key: 'brain', label: '脑体', Icon: GiBrainTentacle },
      { key: 'fishman', label: '鱼人', Icon: GiFishMonster },
      { key: 'octopus', label: '头足', Icon: GiOctopus },
      { key: 'grasp', label: '爪影', Icon: GiMonsterGrasp },
    ],
  },
  {
    label: '亡者',
    items: [
      { key: 'ghost', label: '幽灵', Icon: GiGhost },
      { key: 'spectre', label: '鬼影', Icon: GiSpectre },
      { key: 'zombie', label: '行尸', Icon: GiShamblingZombie },
      { key: 'skull', label: '骸骨', Icon: GiDeathSkull },
    ],
  },
  {
    label: '兽',
    items: [
      { key: 'wolf', label: '狼', Icon: GiWolfHead },
      { key: 'spider', label: '蛛', Icon: GiSpiderAlt },
      { key: 'croc', label: '巨口', Icon: GiCrocJaws },
      { key: 'bat', label: '蝠', Icon: GiBat },
    ],
  },
]

const BY_KEY: Record<FigureKey, IconType> = Object.fromEntries(
  FIGURE_GROUPS.flatMap((g) => g.items.map((i) => [i.key, i.Icon])),
)

/** KP 没挑过时的兜底：己方常人、敌方兜帽。挑过之后一律听 KP 的。 */
export function defaultFigureKey(side: string): FigureKey {
  return side === 'enemy' ? 'cultist' : 'person'
}

export function figureIcon(key: FigureKey): IconType {
  return BY_KEY[key] || GiPerson
}

const STORE_KEY = 'kplab_figures'

type FigureMap = Record<string, FigureKey>

function readAll(): FigureMap {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') as FigureMap
  } catch {
    return {}          // 隐私模式等读不到：当作没挑过，走默认轮廓，别让整页崩掉
  }
}

function slot(sessionId: string, combatantId: string) {
  return `${sessionId}:${combatantId}`
}

export function loadFigures(sessionId: string): FigureMap {
  const all = readAll()
  const prefix = `${sessionId}:`
  return Object.fromEntries(
    Object.entries(all)
      .filter(([k]) => k.startsWith(prefix))
      .map(([k, v]) => [k.slice(prefix.length), v]),
  )
}

export function saveFigure(sessionId: string, combatantId: string, key: FigureKey) {
  try {
    const all = readAll()
    all[slot(sessionId, combatantId)] = key
    localStorage.setItem(STORE_KEY, JSON.stringify(all))
  } catch {
    // 存不下就算了：这一轮仍然生效（调用方持有 state），只是刷新后回到默认
  }
}
