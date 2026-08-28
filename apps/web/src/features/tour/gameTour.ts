// 对局界面的新手导览：遮罩挖洞高亮真实元素 + 气泡说明 + 上下步。
//
// 为什么不是原来那个居中弹窗（OnboardingCoach）：那是一份**说明书**——它告诉你
// 「角色卡在右边」，你还得自己去找。导览直接把那块地方从暗底里挖出来，看一眼就知道
// 在哪、长什么样。
//
// **只导览此刻真在 DOM 里的东西**。投骰卡、战斗面板、幸运询价、分头分栏都要等实际
// 游戏事件才出现，开场根本没有它们可高亮；那几样交给 hints.ts 的一次性提示——
// 在它第一次真出现时才教，玩家正要用到，比开场灌一堆用不上的强。
//
// 元素靠 `data-tour` 属性定位，不复用 class：class 会随样式重构改名，
// 导览会悄无声息地指错地方或整步消失。
import { driver, type DriveStep } from 'driver.js'
import 'driver.js/dist/driver.css'

const SEEN_KEY = 'coc_game_tour_seen_v1'

export function hasSeenGameTour(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === '1'
  } catch {
    return true   // 隐私模式读不到 localStorage：宁可不弹，也别每次进来都弹
  }
}

export function markGameTourSeen() {
  try {
    localStorage.setItem(SEEN_KEY, '1')
  } catch { /* 存不下就算了，不影响使用 */ }
}

/** 房主/多人/真人 KP 相关的步骤在别的席位上不存在——driver 的 skipMissingElement 会跳过它们。 */
const STEPS: DriveStep[] = [
  {
    popover: {
      title: '先花一分钟认认路',
      description:
        '你是调查员，守秘人（KP）由 AI 扮演：它描述场景、扮演所有 NPC、决定什么时候该掷骰。'
        + '下面把界面上你会用到的地方逐个指给你看。',
    },
  },
  {
    element: '[data-tour="input"]',
    popover: {
      title: '用自己的话写行动',
      description:
        '不用记指令，想做什么直接写：「我推开门」「我问他昨晚在哪」。'
        + '<br><b>「」里的是说出口的台词</b>，会单独显示成对话气泡；'
        + '<b>（）里的是场外发言</b>，不进剧情、KP 也不当作角色的言行。',
      side: 'top',
    },
  },
  {
    element: '[data-tour="advance"]',
    popover: {
      title: '写完，交给守秘人',
      description:
        '发言会先攒在本回合，点这里才整批交出去——交出去之前都还能改。'
        + '多人桌要等所有真人都点过，KP 才开始写这一轮。',
      side: 'top',
    },
  },
  {
    element: '[data-tour="party"]',
    popover: {
      title: '同桌的人',
      description: '点谁的名字就翻谁的角色卡。带机器人标记的是 AI 队友，他们会自己行动。',
      side: 'bottom',
    },
  },
  {
    element: '[data-tour="sheet"]',
    popover: {
      title: '你的角色卡',
      description:
        '属性、技能、生命与理智都在这里。<b>技能页点技能名可以主动申请检定</b>——'
        + '想查什么、想说服谁，不必干等 KP 开口。',
      side: 'left',
    },
  },
  {
    element: '[data-tour="map"]',
    popover: {
      title: '换个地方',
      description: '只列出你已经知道的地点。选一处前往，KP 会接着叙述抵达时的见闻。',
      side: 'bottom',
    },
  },
  {
    element: '[data-tour="recap"]',
    popover: {
      title: '忘了前面发生什么',
      description: '战报把本局经历浓缩成小结；旁边的检索能按关键词翻回任意一条历史记录。',
      side: 'bottom',
    },
  },
  {
    element: '[data-tour="usage"]',
    popover: {
      title: '这局花了多少',
      description: '累计的模型上下文用量，随游戏推进单调累增，对应真实 API 花费的趋势。',
      side: 'bottom',
    },
  },
  {
    popover: {
      title: '就这些',
      description:
        '掷骰、战斗、分头行动这些，等它们第一次真的发生时我再就地提示你——'
        + '现在记了也用不上。随时可以点顶栏的问号重看这份导览。',
    },
  },
]

/** 跑一遍对局导览。``onNeedSheet`` 会在开始前调用，用来先把角色卡面板打开（否则那一步没东西可高亮）。 */
export function startGameTour(opts: { onNeedSheet?: () => void } = {}) {
  // 已经有导览/提示在跑就别再起一个。driver 允许同时驱动多个实例，叠起来的后果是
  // 屏幕上并排两个气泡、各记各的步数，点「下一步」只推进最上面那个，另一个永远关不掉。
  if (document.body.classList.contains('driver-active')) return
  opts.onNeedSheet?.()
  // 面板是 React 渲染的，下一帧才在 DOM 里；driver 的 waitForElement 也兜一道。
  requestAnimationFrame(() => {
    driver({
      steps: STEPS,
      showProgress: true,
      progressText: '{{current}} / {{total}}',
      nextBtnText: '下一步',
      prevBtnText: '上一步',
      doneBtnText: '知道了',
      popoverClass: 'coc-tour',
      overlayColor: '#0a0805',
      overlayOpacity: 0.72,
      stagePadding: 6,
      stageRadius: 6,
      smoothScroll: true,
      // 席位不同则按钮不同（房主才有临场角色、真人 KP 没有发言框）——缺哪步跳哪步，
      // 而不是卡在那里等一个永远不会出现的元素。
      skipMissingElement: true,
      waitForElement: 300,
      onDestroyed: markGameTourSeen,
    }).drive()
  })
}
