/**
 * 真人 KP 能发出的动作，以及哪几个值得做成直达按钮。
 *
 * 单独成文件是为了给 HumanKpPanel 腾地方——它已经顶着行数红线，而按约定新增内容该进
 * 新文件，不是把那个数字调大。
 */
import type { ComponentType } from 'react'
import { Bot, WandSparkles } from 'lucide-react'
import { GiRollingDices } from 'react-icons/gi'

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
}

/** 跑团里九成操作就是这三件事，提出来做直达按钮；其余仍在「更多」下拉里，一个不少。 */
// icon 同时收 lucide 组件与 react-icons 的 IconType，取二者都满足的最小形状
export const QUICK_ACTIONS: Array<{ id: KpAction; label: string; icon: ComponentType<{ size?: number }> }> = [
  { id: 'narration', label: '叙事', icon: WandSparkles },
  { id: 'dialogue', label: 'NPC 台词', icon: Bot },
  { id: 'dice_check', label: '检定', icon: GiRollingDices },
]
export const QUICK_IDS = new Set<KpAction>(QUICK_ACTIONS.map((item) => item.id))

/** 旁白与 NPC 台词是「一整段文字」，输入框得写得下；检定那种一行一格的表单撑开只会空旷。 */
export const PROSE_ACTIONS = new Set<KpAction>(['narration', 'dialogue'])
