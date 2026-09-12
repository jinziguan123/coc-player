/**
 * 战棋剪影目录。
 *
 * 为什么需要它：`Combatant` 上只有名字与阵营，没有任何「这是人还是东西」的信息。
 * 按阵营一刀切的话，「不明存在」和「陌生男性」会共用同一个人形轮廓——而这两者在桌上
 * 是完全不同的两回事，扫一眼必须能分清。这种事机器猜不了，由 KP 在工作台上点一下决定
 * （动作 `set_token`），选择存进 combat_state 并随战斗广播发给全桌。
 */
import type { ComponentType } from 'react'
import {
  GiBat, GiBrainTentacle, GiCowled, GiCrocJaws, GiCultist, GiDeathSkull,
  GiDoctorFace, GiFedora, GiFishMonster, GiGhost, GiHoodedFigure, GiMonsterGrasp,
  GiOctopus, GiPerson, GiPoliceOfficerHead, GiShamblingZombie, GiSpectre,
  GiSpiderAlt, GiSuckeredTentacle, GiSwordman, GiTentacleStrike, GiWolfHead,
} from 'react-icons/gi'

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

/** 取某参战方该用的剪影：KP 挑过就用它，否则按阵营回落。 */
export function figureOf(c: { figure?: string; side: string }): IconType {
  return figureIcon(c.figure || defaultFigureKey(c.side))
}
