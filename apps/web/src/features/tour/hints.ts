// 一次性就地提示：某个功能**第一次真的出现**时，高亮它并讲一句。
//
// 投骰卡、战斗面板、幸运询价、分头分栏都是事件驱动的——开场导览时它们根本不在 DOM 里，
// 无从高亮。而这几样恰恰是最需要教的。所以换个时机：等它第一次冒出来，玩家正要用到，
// 就地高亮讲一句。这比开场把所有功能灌一遍留得住得多。
//
// 每个 key 一辈子只弹一次（localStorage 记住）。读不到 localStorage 就当作已看过——
// 隐私模式下宁可不提示，也别每来一次弹一次。
import { driver } from 'driver.js'
import 'driver.js/dist/driver.css'

const PREFIX = 'coc_hint_seen::'

export type HintKey =
  | 'check-request'   // 第一张待投骰卡
  | 'dice-result'     // 第一张检定结果卡
  | 'luck-offer'      // 第一次可以花幸运翻盘
  | 'combat'          // 第一次进入结构化战斗
  | 'split-party'     // 第一次分头行动

interface Hint {
  title: string
  description: string
}

const HINTS: Record<HintKey, Hint> = {
  'check-request': {
    title: '轮到你掷骰',
    description:
      '守秘人要你过一次检定了。<b>点「投骰」，系统替你掷</b>——骰子结果是权威的，'
      + 'KP 不能改也不会偷看。旁边那行「因：…」写着这一掷为什么要投。',
  },
  'dice-result': {
    title: '怎么读这张结果卡',
    description:
      '大数字是<b>掷出的点数</b>，斜杠后面是<b>要掷到多少才算过</b>——'
      + 'CoC 是<b>越低越好</b>，掷出的点数小于等于目标就成功。'
      + '右边的标签是达成等级；如果这一掷带了奖励骰或惩罚骰，也会标在那儿。',
  },
  'luck-offer': {
    title: '差一点点，可以用幸运买回来',
    description:
      '花幸运点抵掉差的那几点，把这次失败变成成功。花掉就不回来了，'
      + '而且<b>买来的成功不算技能成长</b>——值不值由你定。不买就点「放弃」，'
      + '这张卡不点掉，后面的剧情不会往下走。',
  },
  combat: {
    title: '进入战斗',
    description:
      '战斗按先攻顺序一轮一轮来。轮到你时这里会给出可选动作，'
      + '不轮到你时看着就行。想脱身可以选逃跑——CoC 里正面硬刚往往不是最优解。',
  },
  'split-party': {
    title: '队伍分头了',
    description:
      '现在分成两列，各是一处场景里发生的事。<b>你只看得到自己那一列的实况</b>，'
      + '另一列的人在别处经历着别的事——他们那边的动静，要等碰头了才知道。',
  },
}

function seenKey(key: HintKey) {
  return PREFIX + key
}

export function hasSeenHint(key: HintKey): boolean {
  try {
    return localStorage.getItem(seenKey(key)) === '1'
  } catch {
    return true
  }
}

export function markHintSeen(key: HintKey) {
  try {
    localStorage.setItem(seenKey(key), '1')
  } catch { /* 存不下就算了 */ }
}

/**
 * 若该提示没看过、且目标元素已在页面上，就高亮它并讲一句。
 *
 * 立刻标记为已看过（而不是等关闭时）：这些卡片往往转瞬即换（投完骰就变成结果卡），
 * 等回调可能永远等不到，那样下一局又会弹一次。
 */
export function showHintOnce(key: HintKey, selector: string) {
  if (hasSeenHint(key)) return
  // 已经有导览/别的提示在跑：driver 同时驱动两个实例会打架。这里**不标记已看过**，
  // 直接让位——下次这张卡再触发时还会来。
  if (document.body.classList.contains('driver-active')) return
  if (!document.querySelector(selector)) return
  markHintSeen(key)
  driver({
    steps: [{ element: selector, popover: HINTS[key] }],
    showProgress: false,
    doneBtnText: '知道了',
    showButtons: ['close'],
    popoverClass: 'coc-tour',
    overlayColor: '#0a0805',
    overlayOpacity: 0.68,
    stagePadding: 6,
    stageRadius: 6,
    smoothScroll: true,
    waitForElement: 200,
  }).drive()
}

/** 只在测试里用：把所有一次性提示的记录清掉。 */
export function resetHints() {
  try {
    for (const key of Object.keys(HINTS)) localStorage.removeItem(PREFIX + key)
  } catch { /* 忽略 */ }
}
