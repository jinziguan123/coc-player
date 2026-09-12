/**
 * 真人 KP 能发出的动作，以及哪几个值得做成直达按钮。
 *
 * 单独成文件是为了给 HumanKpPanel 腾地方——它已经顶着行数红线，而按约定新增内容该进
 * 新文件，不是把那个数字调大。
 */
import type { ComponentType } from 'react'
import { Bot, WandSparkles } from 'lucide-react'
import {
  GiBrain, GiCancel, GiCheckMark, GiCrossedSwords, GiFootTrip, GiHeartPlus,
  GiPadlock, GiRollingDices, GiScrollUnfurled, GiTheaterCurtains,
} from 'react-icons/gi'

export type KpAction =
  | 'narration'
  | 'dialogue'
  | 'dice_check'
  | 'opposed_check'
  | 'generic_roll'
  | 'san_check'
  | 'scene_change'
  | 'set_flag'
  | 'clear_flag'
  | 'handout'
  | 'hp_change'
  | 'start_combat'
  | 'unlock_place'
  | 'move_actor'

export const ACTION_LABELS: Record<KpAction, string> = {
  narration: '发布叙事',
  dialogue: 'NPC 台词',
  dice_check: '发起检定',
  opposed_check: '对抗检定',
  generic_roll: '通用骰',
  san_check: '理智检定',
  scene_change: '切换场景',
  set_flag: '推进标志',
  clear_flag: '解除标志',
  handout: '发放手书',
  hp_change: '结算 HP',
  start_combat: '开始战斗',
  unlock_place: '解锁地点',
  move_actor: '移动角色',
}

/**
 * 全部动作平铺，按「跑团里用到的频次」排，不按枚举顺序——枚举顺序对 KP 没有意义。
 *
 * 此前只有三件事是直达按钮，其余九个压在一个「更多…」下拉里。跑团时眼睛在桌上、
 * 手在键盘上，多一次下拉就是多一次找；而那九个里有「发手书」「切场景」「开战」
 * 这种并不冷门的动作。现在一个不藏。
 */
// icon 同时收 lucide 组件与 react-icons 的 IconType，取二者都满足的最小形状
type ActionIcon = ComponentType<{ size?: number }>
export const ACTION_CHIPS: Array<{ id: KpAction; label: string; icon: ActionIcon }> = [
  { id: 'narration', label: '叙事', icon: WandSparkles },
  { id: 'dialogue', label: 'NPC 台词', icon: Bot },
  { id: 'dice_check', label: '检定', icon: GiRollingDices },
  { id: 'san_check', label: '理智', icon: GiBrain },
  { id: 'generic_roll', label: '通用骰', icon: GiRollingDices },
  { id: 'opposed_check', label: '对抗', icon: GiCrossedSwords },
  { id: 'handout', label: '手书', icon: GiScrollUnfurled },
  { id: 'scene_change', label: '切场景', icon: GiTheaterCurtains },
  { id: 'unlock_place', label: '解锁地点', icon: GiPadlock },
  { id: 'move_actor', label: '移动角色', icon: GiFootTrip },
  { id: 'hp_change', label: '结算 HP', icon: GiHeartPlus },
  { id: 'start_combat', label: '开战', icon: GiCrossedSwords },
  { id: 'set_flag', label: '推进标志', icon: GiCheckMark },
  { id: 'clear_flag', label: '解除标志', icon: GiCancel },
]

/**
 * 斜杠命令：输入框里打 `/` 唤起。
 *
 * 手不用离开键盘去够鼠标——这是「产出顺手」最直接的一条。缩写取「打两个字就够」的长度，
 * KP 不该背全称。
 */
export const ACTION_COMMANDS: Array<{ cmd: string; action: KpAction; hint: string }> = [
  { cmd: '叙事', action: 'narration', hint: '发布一段旁白' },
  { cmd: '台词', action: 'dialogue', hint: '让某个 NPC 说话' },
  { cmd: '检定', action: 'dice_check', hint: '要一次技能检定' },
  { cmd: '理智', action: 'san_check', hint: '理智检定' },
  { cmd: '骰', action: 'generic_roll', hint: '临场裁定用的通用骰' },
  { cmd: '对抗', action: 'opposed_check', hint: '两方对着掷' },
  { cmd: '手书', action: 'handout', hint: '发一份信件 / 剪报 / 日记' },
  { cmd: '场景', action: 'scene_change', hint: '把全桌挪到另一个场景' },
  { cmd: '解锁', action: 'unlock_place', hint: '把一个地点对玩家公开' },
  { cmd: '移动', action: 'move_actor', hint: '把某人挪到另一个地点' },
  { cmd: 'hp', action: 'hp_change', hint: '结算伤害或治疗' },
  { cmd: '开战', action: 'start_combat', hint: '进入战斗轮' },
  { cmd: '标志', action: 'set_flag', hint: '推进剧情标志' },
  { cmd: '解除', action: 'clear_flag', hint: '解除剧情标志' },
]

/** 旁白与 NPC 台词是「一整段文字」，输入框得写得下；检定那种一行一格的表单撑开只会空旷。 */
export const PROSE_ACTIONS = new Set<KpAction>(['narration', 'dialogue'])
