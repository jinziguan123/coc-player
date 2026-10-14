import { useEffect, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api, getServerUrl } from '@/api/client'
import { GiRollingDices } from 'react-icons/gi'
import {
  BookOpen,
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Crosshair,
  Image,
  Info,
  Maximize2,
  Move,
  NotebookPen,
  RefreshCw,
  Search,
  Send,
  WandSparkles,
  X,
} from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { KpActionForm } from './KpActionForm'
import { usePartyVitals } from '@/features/game-session/usePartyVitals'
import { useKpPrefill } from '@/stores/kpPrefill'
import { KpPartyMonitor } from './KpPartyMonitor'
import { KpSubTabs } from './KpSubTabs'
import { useDragResize } from '@/components/ui/useDragResize'
import { useDictation } from './useDictation'
import { useSlashCommands } from './useSlashCommands'
import { ACTION_LABELS, PROSE_ACTIONS, REFERENCE_IDS, type KpAction } from './kpActions'
import { KpActionBar } from './KpActionBar'

type PanelTab = 'tools' | 'advisor' | 'assets' | 'director' | 'source'

interface CatalogItem {
  id: string
  name: string
}

interface DirectorSignals {
  spotlight_starved: string[]
  stuck: boolean
  stuck_turns: number
  unresolved_threads: string[]
  monotonous: boolean
  promotable_npcs?: string[]   // 反复出场、值得收编为正式配角的临场龙套
  ending_ready?: boolean       // 主线看起来已走完，可提示玩家收束（结束与否仍由玩家决定）
}

interface ImageSuggestion {
  key: string
  title: string
  prompt: string
  image_kind: string
  image_item_id?: string
  image_field?: string
  preview_url?: string
  source_event_id?: string
}

interface Workspace {
  notes: string
  auto_ai_teammates: boolean
  player_missing?: boolean
  image_suggestions?: ImageSuggestion[]
  has_ai_teammates: boolean
  has_unprocessed_player_turn: boolean
  signals: DirectorSignals
  catalogs: {
    characters: CatalogItem[]
    scenes: CatalogItem[]
    npcs: CatalogItem[]
    handouts: CatalogItem[]
  }
}

interface ModuleSource {
  id: string
  title: string
  description: string
  raw_content: string
  world_setting: Record<string, unknown>
  truth: string
  scenes: unknown[]
  npcs: unknown[]
  clues: unknown[]
  triggers: unknown[]
  handouts: unknown[]
  maps: unknown[]
  rag_status: string
  chunks: Array<{ ordinal: number; scene_hint?: string | null; text: string }>
}

function AdvisorHelp({ label, text }: { label: string; text: string }) {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`${label}说明`}
            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-[var(--color-bg-secondary)] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-accent)]"
            style={{ color: 'var(--color-text-accent)' }}
          >
            <Info size={15} aria-hidden="true" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-72 whitespace-normal leading-relaxed">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

interface AdvisorPlan {
  player_intent?: string
  requires_check?: boolean
  auto_outcome?: string
  auto_outcome_reason?: string
  check?: { skill?: string; difficulty?: string; chars?: string; reason?: string; bonus?: number; penalty?: number }
  clue_policy?: { candidate_clue_ids?: string[]; reveal_level?: string; notes?: string }
  npc_policy?: { speakers?: string[]; reaction?: string }
  scene_policy?: { scene_change?: string | null; set_flags?: string[]; clear_flags?: string[] }
  combat?: { should_start?: boolean; enemies?: string[]; trigger?: string }
  sanity?: { trigger?: boolean; source?: string; success_loss?: string; failure_loss?: string; witnesses?: string[] }
  direction?: { pacing?: string; spotlight?: string[]; nudge?: string; foreshadow?: string }
  narration_brief?: string[]
}

interface LookupHit {
  text: string
  page?: number
  scene_hint?: string | null
  score?: number
  rulebook_id?: string
  ordinal?: number
}

interface Props {
  sessionId: string
  turnReady?: boolean
  /** 'inline' 内联在聊天列（旧版）；'sidebar' 独立右侧栏，撑满高度、内容区自己滚。
   *  侧栏形态下收起由外层的窄条接管，面板不再自绘收起按钮。 */
  variant?: 'inline' | 'sidebar'
  onCollapse?: () => void
  /** 场景速查等由外层提供的附属块（需要局内地点表，那份数据在页面上）。
   *  宽屏下它与监控列同列，窄屏下顺次堆在写作台之上。 */
  aside?: ReactNode
  /** 值班台形态：监控列与场景速查由页面左侧栏接管，坞里只保留写作台，不再重复渲染。 */
  hideSituation?: boolean
}


/**
 * 标签压成两层：一级只有「主持」和「资料」。
 *
 * 从前五个标签平铺（主持/参谋/配图与队友/导演台/模组资料），在 334px 的侧栏里要换行，
 * 且个个同等份量——可 KP 九成时间只待在主持页，另外四个是**查阅**，不是并列的工作台。
 * 平铺的结果是主持这一档被另外四个稀释掉了。
 */

function imageUrl(src: string): string {
  if (/^https?:\/\//i.test(src)) return src
  return `${getServerUrl()}${src}`
}

function highlightText(text: string, query: string) {
  const needle = query.trim()
  if (!needle) return text
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'ig'))
  return parts.map((part, index) => (
    part.toLocaleLowerCase() === needle.toLocaleLowerCase()
      ? (
          <mark
            key={`${part}-${index}`}
            className="rounded px-0.5 font-semibold"
            style={{ background: 'var(--color-accent)', color: 'var(--color-on-accent)' }}
          >
            {part}
          </mark>
        )
      : part
  ))
}

export function HumanKpPanel({ sessionId, turnReady = false, variant = 'inline', onCollapse, aside, hideSituation = false }: Props) {
  const sidebar = variant === 'sidebar'
  // 侧栏形态没有「内联收起」这回事——整栏的显隐由外层窄条控制，此处恒展开。
  const [collapsedInline, setCollapsedInline] = useState(false)
  const collapsed = sidebar ? false : collapsedInline
  const setCollapsed = setCollapsedInline
  const [tab, setTab] = useState<PanelTab>('tools')
  /** 点「资料」回到上次看的那一页，而不是每次都从参谋重新找。 */
  const [lastRefTab, setLastRefTab] = useState<PanelTab>('advisor')
  const [action, setAction] = useState<KpAction>('narration')
  const [busy, setBusy] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [lastActionResult, setLastActionResult] = useState('')
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  // 体征重拉的时机：KP 每发一个动作（可能扣血扣 SAN）、以及一回合收口时
  const [vitalsTick, setVitalsTick] = useState(0)
  useEffect(() => { setVitalsTick((t) => t + 1) }, [turnReady])
  // 全队生命体征：KP 每分钟都要知道谁快死了、谁理智见底，不该藏在角色卡里一次只看一个。
  // 值班台形态（hideSituation）下这份数据由页面左侧栏的监控列负责，坞里不再重复拉取。
  const party = usePartyVitals(hideSituation ? undefined : workspace?.catalogs.characters?.map(
    (c) => ({ character_id: c.id, character_name: c.name, role: 'human' }),
  ), vitalsTick)
  // 斜杠命令：叙事框里打 / 直接切动作，手不离键盘（见 useSlashCommands）
  const slash = useSlashCommands({
    text: fields.content || '',
    onPick: (next) => { setAction(next); setFields({}) },
  })
  const [source, setSource] = useState<ModuleSource | null>(null)
  const [sourceView, setSourceView] = useState<'raw' | 'parsed'>('raw')
  const [sourceOpen, setSourceOpen] = useState(false)
  const [sourceQuery, setSourceQuery] = useState('')
  const [sourceTargetOrdinal, setSourceTargetOrdinal] = useState<number | null>(null)
  // 资料悬浮窗的拖动与缩放：与 KP 业务无关的通用行为，见 ui/useDragResize
  const {
    position: sourcePosition, size: sourceSize,
    drag: sourceDrag, resize: sourceResize, reset: resetSourceWindow,
  } = useDragResize({ width: 640, height: 680 })
  const [notes, setNotes] = useState('')
  const [draftInstruction, setDraftInstruction] = useState('')
  const [draft, setDraft] = useState('')
  const [planFocus, setPlanFocus] = useState('')
  const [plan, setPlan] = useState<AdvisorPlan | null>(null)
  const [lookupScope, setLookupScope] = useState<'rule' | 'module'>('module')
  const [lookupQuery, setLookupQuery] = useState('')
  const [lookupHits, setLookupHits] = useState<LookupHit[]>([])
  const [lookupOpen, setLookupOpen] = useState(false)
  const [imageTitle, setImageTitle] = useState('当前场景')
  const [imagePrompt, setImagePrompt] = useState('')
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewSuggestionKey, setPreviewSuggestionKey] = useState('')
  // 语音转文字：与工作台其余部分无耦合，逻辑在 useDictation
  const { dictating, toggle: toggleDictation } = useDictation(
    (chunk) => setFields((current) => ({ ...current, content: `${current.content || ''}${chunk}` })),
  )
  const sourceChunkRefs = useRef<Record<number, HTMLDivElement | null>>({})

  const refreshWorkspace = async () => {
    try {
      const result = await api.get<Workspace>(`/sessions/${sessionId}/kp/workspace`)
      setWorkspace(result)
      setNotes(result.notes)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'KP 工作区加载失败')
    }
  }

  const openModuleSource = async (force = false): Promise<ModuleSource | null> => {
    setSourceOpen(true)
    if (source && !force) return source
    setBusy('source')
    try {
      const loaded = await api.get<ModuleSource>(`/sessions/${sessionId}/kp/source`)
      setSource(loaded)
      return loaded
    } catch (error) {
      setSourceOpen(false)
      toast.error(error instanceof Error ? error.message : '模组资料加载失败')
      return null
    } finally {
      setBusy('')
    }
  }


  useEffect(() => {
    void refreshWorkspace()
  }, [sessionId])

  useEffect(() => {
    if (tab === 'director' || tab === 'assets') void refreshWorkspace()
  }, [tab, turnReady])

  useEffect(() => {
    sourceChunkRefs.current = {}
    resetSourceWindow()
    setSourceQuery('')
    setSourceTargetOrdinal(null)
    setSource(null)
    setSourceOpen(false)
  }, [sessionId, resetSourceWindow])

  useEffect(() => {
    if (!sourceOpen || sourceView !== 'parsed' || sourceTargetOrdinal === null) return
    const frame = window.requestAnimationFrame(() => {
      sourceChunkRefs.current[sourceTargetOrdinal]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [sourceOpen, sourceView, sourceTargetOrdinal, source])

  const setField = (key: string, value: string) => {
    setFields((current) => ({ ...current, [key]: value }))
  }

  /** 采纳参谋建议：切回「主持」并把建议参数预填进表单，交由 KP 过目/微调后再发布。
   *  与旁边的「执行」不同 —— 执行是立刻广播给全桌，采纳只是把字段填好，还没发出去。 */
  const adoptToTools = (nextAction: KpAction, nextFields: Record<string, string | undefined>) => {
    const clean: Record<string, string> = {}
    for (const [key, value] of Object.entries(nextFields)) {
      const text = (value ?? '').toString().trim()
      if (text) clean[key] = text
    }
    setAction(nextAction)
    setFields(clean)
    setTab('tools')
    toast.success(`已填入「${ACTION_LABELS[nextAction]}」，确认后再发布`)
  }

  const setDiceAdjustment = (prefix: string, bonus: string, penalty: string) => {
    setFields((current) => ({
      ...current,
      [`${prefix}bonus`]: bonus,
      [`${prefix}penalty`]: penalty,
    }))
  }

  const postAction = async (nextAction: KpAction, payload: Record<string, unknown>, success: string) => {
    setBusy(`action:${nextAction}`)
    try {
      const response = await api.post<{ result?: string }>(`/sessions/${sessionId}/kp/action`, { action: nextAction, payload })
      const result = response.result || success
      setLastActionResult(result)
      setVitalsTick((t) => t + 1)   // 动作可能改了血量/理智，监控列跟着刷
      toast.success(success)
      return result
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'KP 动作执行失败')
      return null
    } finally {
      setBusy('')
    }
  }

  const submit = async () => {
    if (busy) return
    const defaultActor = workspace?.catalogs.characters?.[0]
      ? `character:${workspace.catalogs.characters[0].id}`
      : ''
    const nextFields = { ...fields }
    if (action === 'dice_check' && !nextFields.char) nextFields.char = defaultActor
    if (action === 'opposed_check' && !nextFields.a) nextFields.a = defaultActor
    const payload = Object.fromEntries(
      Object.entries(nextFields).filter(([, value]) => value.trim()),
    )
    const doneText = action === 'end_combat' ? '战斗已结束' : `${ACTION_LABELS[action]}已发布`
    if (await postAction(action, payload, doneText)) {
      // 连续 NPC 台词时保留说话人，只清空上一句正文。
      setFields(action === 'dialogue' ? { npc_id: fields.npc_id || '' } : {})
    }
  }

  const generateDraft = async () => {
    setBusy('draft')
    try {
      const result = await api.post<{ draft: string }>(`/sessions/${sessionId}/kp/advisor/draft`, {
        instruction: draftInstruction,
      })
      setDraft(result.draft)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '叙事草稿生成失败')
    } finally {
      setBusy('')
    }
  }

  const generatePlan = async () => {
    setBusy('plan')
    try {
      const result = await api.post<{ plan: AdvisorPlan }>(`/sessions/${sessionId}/kp/advisor/plan`, {
        focus: planFocus,
      })
      setPlan(result.plan)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '裁定建议生成失败')
    } finally {
      setBusy('')
    }
  }

  const runLookup = async () => {
    if (!lookupQuery.trim()) return
    setBusy('lookup')
    try {
      const params = new URLSearchParams({ scope: lookupScope, q: lookupQuery.trim() })
      const result = await api.get<{ hits: LookupHit[] }>(`/sessions/${sessionId}/kp/lookup?${params}`)
      setLookupHits(result.hits)
      setLookupOpen(true)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '资料检索失败')
    } finally {
      setBusy('')
    }
  }

  const openModuleAtHit = async (hit: LookupHit) => {
    setLookupOpen(false)
    setSourceView('parsed')
    setSourceQuery(hit.text.slice(0, 120))
    setSourceTargetOrdinal(typeof hit.ordinal === 'number' ? hit.ordinal : null)
    const loaded = await openModuleSource()
    if (!loaded?.chunks?.length) setSourceView('raw')
  }

  const updateAutoTeammates = async (enabled: boolean) => {
    setBusy('workspace')
    try {
      const result = await api.patch<Workspace>(`/sessions/${sessionId}/kp/workspace`, {
        auto_ai_teammates: enabled,
      })
      setWorkspace(result)
      toast.success(enabled ? '已开启 AI 队友自动行动' : '已关闭 AI 队友自动行动')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '设置保存失败')
    } finally {
      setBusy('')
    }
  }

  const runTeamTurn = async () => {
    setBusy('team')
    try {
      await api.post(`/sessions/${sessionId}/kp/team-turn`)
      toast.success('AI 队友正在处理本回合')
      setWorkspace((current) => current ? { ...current, has_unprocessed_player_turn: false } : current)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'AI 队友行动失败')
    } finally {
      setBusy('')
    }
  }

  const endTurn = async () => {
    setBusy('end-turn')
    try {
      await api.post(`/sessions/${sessionId}/kp/end-turn`)
      toast.success('已开放下一回合')
      await refreshWorkspace()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '开放下一回合失败')
    } finally {
      setBusy('')
    }
  }

  const generateImage = async (suggestion?: ImageSuggestion) => {
    const title = suggestion?.title || imageTitle
    const prompt = suggestion?.prompt || imagePrompt
    if (!prompt.trim()) return
    if (suggestion) {
      setImageTitle(title)
      setImagePrompt(prompt)
      setPreviewSuggestionKey(suggestion.key)
      if (suggestion.preview_url) {
        setPreviewUrl(suggestion.preview_url)
        toast.success('已载入缓存预览，仅 KP 可见')
        return
      }
    }
    setBusy('image')
    try {
      const result = await api.post<{ url: string }>(`/sessions/${sessionId}/kp/images/preview`, {
        prompt,
        title,
      })
      setPreviewUrl(result.url)
      toast.success('图片已生成，仅 KP 可见')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '图片生成失败')
    } finally {
      setBusy('')
    }
  }

  const publishImage = async () => {
    setBusy('publish-image')
    try {
      await api.post(`/sessions/${sessionId}/kp/images/publish`, {
        url: previewUrl,
        title: imageTitle,
        suggestion_key: previewSuggestionKey,
      })
      toast.success('配图已发布给全桌')
      setPreviewUrl('')
      setPreviewSuggestionKey('')
      await refreshWorkspace()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '配图发布失败')
    } finally {
      setBusy('')
    }
  }

  const saveNotes = async () => {
    setBusy('notes')
    try {
      const result = await api.patch<Workspace>(`/sessions/${sessionId}/kp/workspace`, { notes })
      setWorkspace(result)
      toast.success('KP 笔记已保存')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '笔记保存失败')
    } finally {
      setBusy('')
    }
  }

  const signals = workspace?.signals
  // 常驻信号灯的内容：只留「此刻确实成立」的那几条，没有信号就整条不出现。
  // 值班台监控列的预填请求：左侧栏 KpPartyMonitor 发来「对谁做什么」→ 走 adoptToTools
  // 把动作与参数填进写作台，KP 过目后再发（沿用「采纳建议」那条路，不是直接广播）。
  const prefillReq = useKpPrefill((s) => s.request)
  useEffect(() => {
    if (!prefillReq) return
    adoptToTools(prefillReq.action, prefillReq.fields)
    useKpPrefill.getState().consume()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- adoptToTools 每次渲染都是新引用
  }, [prefillReq])

  const signalChips: Array<{ key: string; label: string; tone: string }> = []
  if (signals?.stuck) {
    signalChips.push({
      key: 'stuck',
      label: `卡关 ${signals.stuck_turns} 回合`,
      tone: 'chip--danger',
    })
  }
  if (signals?.spotlight_starved?.length) {
    signalChips.push({
      key: 'spotlight',
      label: `冷场 ${signals.spotlight_starved.join('、')}`,
      tone: 'chip--accent',
    })
  }
  if (signals?.monotonous) {
    signalChips.push({ key: 'monotonous', label: '节奏单调', tone: 'chip--accent' })
  }
  if (signals?.ending_ready) {
    // 放在最前：这是「该收尾了」，比冷场/节奏更值得 KP 立刻看到
    signalChips.unshift({ key: 'ending', label: '主线已走完，可收尾', tone: 'chip--success' })
  }
  if (signals?.promotable_npcs?.length) {
    signalChips.push({
      key: 'promotable',
      label: `可收编 ${signals.promotable_npcs.join('、')}`,
      tone: 'chip--accent',
    })
  }
  if (signals?.unresolved_threads?.length) {
    signalChips.push({
      key: 'threads',
      label: `待回收 ${signals.unresolved_threads.length}`,
      tone: '',
    })
  }

  const sceneLabel = (sceneId: string | null | undefined) => (
    workspace?.catalogs.scenes.find((scene) => scene.id === sceneId)?.name || sceneId || '模组原文片段'
  )
  const sourceSections: Array<[string, unknown]> = [
    ['世界设定', source?.world_setting],
    ['幕后真相', source?.truth],
    ['场景', source?.scenes],
    ['NPC', source?.npcs],
    ['线索', source?.clues],
    ['触发器', source?.triggers],
    ['手书', source?.handouts],
    ['地图', source?.maps],
  ]
  const normalizedSourceQuery = sourceQuery.trim().toLocaleLowerCase()
  const sourceChunks = source?.chunks || []
  const visibleSourceChunks = sourceChunks.filter((chunk) => (
    !normalizedSourceQuery
    || chunk.text.toLocaleLowerCase().includes(normalizedSourceQuery)
    || String(chunk.scene_hint || '').toLocaleLowerCase().includes(normalizedSourceQuery)
  ))

  return (
    <section className={`kp-console rounded-md px-3 py-2 ${sidebar ? 'kp-console--sidebar' : 'mx-3 mb-2'}`}>
      {/* 抬头：身份徽标 + 两个一级标签 + 收起。「开放行动」挪去下面的回合条——
          它是「把回合交还给玩家」，属于本回合的事，不该和导航挤在同一排。 */}
      <div className={`${collapsed ? '' : 'mb-2'} flex items-center gap-1.5`}>
        <span className="kp-console-badge mr-1 whitespace-nowrap">真人 KP</span>
        {!collapsed && (
          <div className="kp-tabs inline-flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => setTab('tools')}
              className={`kp-tab inline-flex items-center gap-1 whitespace-nowrap ${tab === 'tools' ? 'kp-tab--active' : ''}`}
              aria-pressed={tab === 'tools'}
            >
              <WandSparkles size={13} /> 主持
            </button>
            <button
              type="button"
              onClick={() => setTab(lastRefTab)}
              className={`kp-tab inline-flex items-center gap-1 whitespace-nowrap ${REFERENCE_IDS.has(tab) ? 'kp-tab--active' : ''}`}
              aria-pressed={REFERENCE_IDS.has(tab)}
              title="参谋、配图与队友、导演台、模组资料"
            >
              <BookOpen size={13} /> 资料
            </button>
          </div>
        )}
        <button
          type="button"
          onClick={() => (sidebar ? onCollapse?.() : setCollapsed((value) => !value))}
          className="btn-secondary ml-auto !p-1.5"
          aria-expanded={!collapsed}
          title={sidebar ? '收起 KP 控制台' : collapsed ? '展开 KP 控制台' : '收起 KP 控制台'}
        >
          {sidebar ? <ChevronRight size={15} /> : collapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
        </button>
      </div>

      {/* 回合条：本回合还差什么 + 把回合交还给玩家。常驻（不随标签切换消失）——
          冷场/卡关/待回收都是**时效**信号，KP 正在翻模组资料时同样要看得见。 */}
      {!collapsed && (
        <div className="kp-turn-bar">
          <button
            type="button"
            onClick={() => setTab(signalChips.length ? 'director' : tab)}
            className="kp-turn-signals"
            disabled={!signalChips.length}
            title={signalChips.length ? '点击查看导演台详情' : undefined}
          >
            <Info size={12} className="flex-shrink-0" />
            {signalChips.length
              ? signalChips.map((chip) => (
                <span key={chip.key} className={`chip ${chip.tone}`}>{chip.label}</span>
              ))
              : <span className="kp-turn-idle">本回合没有待办信号</span>}
          </button>
          <button
            type="button"
            onClick={() => void endTurn()}
            disabled={!!busy}
            className="btn-primary inline-flex flex-shrink-0 items-center gap-1 whitespace-nowrap !px-2 !py-1 text-xs"
            title="结束 KP 回合，把行动权交还给玩家"
          >
            <Send size={13} /> {busy === 'end-turn' ? '处理中…' : '开放行动'}
          </button>
        </div>
      )}

      {/* 分栏：宽屏「看的」在左、「做的」在右；窄屏顺次堆叠。见 index.css */}
      <div className="kp-console-split">
      {/* 态势列：全队体征 + 场景速查，只在「主持」档出现——翻资料时它就是干扰。
          行内动作走 adoptToTools（填进表单、不直接发出），裁定权仍在 KP 手里。
          值班台形态下这列由页面左侧栏接管，坞里不再渲染。 */}
      {!collapsed && tab === 'tools' && !hideSituation && (
        <div className="kp-situation">
          <KpPartyMonitor party={party} onPrefill={(next, fields) => adoptToTools(next, fields)} />
          {aside}
        </div>
      )}
      {/* 资料的二级标签：只在「资料」这一档下出现 */}
      {!collapsed && REFERENCE_IDS.has(tab) && (
        <KpSubTabs tab={tab} onPick={(id) => {
          setTab(id)
          setLastRefTab(id)
          // 「模组资料」那一档配的是悬浮窗，再点一次即收起
          if (id === 'source') { if (sourceOpen) setSourceOpen(false); else void openModuleSource() }
        }} />
      )}

      {/* 侧栏形态：抬头与回合条常驻，其下整体滚动 */}
      <div className={sidebar ? 'kp-console-body' : ''}>

      {!collapsed && tab === 'tools' && (
        <div>
          {workspace?.player_missing && (
            <div className="mb-2 rounded border px-2 py-1.5 text-xs" style={{ borderColor: 'var(--color-warning)', color: 'var(--color-warning)' }}>
              当前还没有真人玩家角色。玩家入座并选择角色后，参谋才能生成针对具体玩家的裁定建议。
            </div>
          )}
          {/* 写作台：动作切换 → 输入 → 发出，三段收进同一张卡里。
              从前它们是三块各自漂浮的元素，KP 得自己把「我在干什么」拼起来。
              切动作只换输入区的形态，不换页面。 */}
          <div className="kp-composer">
          {/* 全部动作直达、按四组排开（见 kpActions.ACTION_GROUPS）——一个不藏 */}
          <KpActionBar action={action} onPick={(id) => { setAction(id); setFields({}) }} />
          <div className={`kp-composer-form flex flex-wrap gap-2${PROSE_ACTIONS.has(action) ? ' kp-composer-form--prose' : ''}`}>
            <KpActionForm
              action={action}
              fields={fields}
              workspace={workspace}
              setField={setField}
              setDiceAdjustment={setDiceAdjustment}
              submit={submit}
              slash={slash}
              dictating={dictating}
              toggleDictation={toggleDictation}
            />
          </div>
          {/* 主按钮单独一行、靠右：它是这条流水线的出口，文案随当前动作变 */}
          <div className="kp-composer-submit">
            <button onClick={() => void submit()} disabled={!!busy} className="btn-primary inline-flex items-center gap-1 text-xs">
              {(['dice_check', 'opposed_check', 'generic_roll'] as KpAction[]).includes(action) ? <GiRollingDices size={13} /> : <Send size={13} />}
              {busy ? '处理中…' : ACTION_LABELS[action]}
            </button>
          </div>
          </div>
          {lastActionResult && (
            <div className="mt-2 flex items-start gap-2 border-t pt-2 text-xs" style={{ borderColor: 'var(--color-border)', color: 'var(--color-text-secondary)' }} aria-live="polite">
              <strong className="shrink-0" style={{ color: 'var(--color-text-primary)' }}>最近结果</strong>
              <span className="min-w-0 flex-1 whitespace-pre-wrap">{lastActionResult}</span>
              <button type="button" onClick={() => setLastActionResult('')} className="btn-secondary !p-1" title="清除最近结果"><X size={12} /></button>
            </div>
          )}
        </div>
      )}

      {!collapsed && tab === 'advisor' && (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2 border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
            <input value={planFocus} onChange={(event) => setPlanFocus(event.target.value)} placeholder="裁定重点（可选）" className="input min-w-48 flex-1 text-xs" />
            <button onClick={() => void generatePlan()} disabled={!!busy} className="btn-secondary inline-flex items-center gap-1 text-xs">
              <Bot size={13} /> {busy === 'plan' ? '分析中…' : '生成裁定建议'}
            </button>
            <AdvisorHelp
              label="裁定建议"
              text="根据当前场景、玩家行动和可用规则生成仅 KP 可见的裁定参考。生成建议不会改变游戏；只有点击“照此发起”或“执行”后，对应检定或状态变化才会生效。"
            />
            {plan && (
              <div className="w-full space-y-1 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                <div><strong style={{ color: 'var(--color-text-primary)' }}>玩家意图：</strong>{plan.player_intent || '未识别'}</div>
                {plan.requires_check && plan.check?.skill && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>建议检定：{plan.check.skill} / {plan.check.difficulty || 'normal'} {plan.check.reason && `· ${plan.check.reason}`}</span>
                    <button
                      className="btn-secondary !px-2 !py-0.5"
                      title="填入主持表单，可改难度/对象后再发起"
                      onClick={() => adoptToTools('dice_check', {
                        skill: plan.check?.skill,
                        difficulty: plan.check?.difficulty,
                        char: plan.check?.chars,
                        bonus: plan.check?.bonus ? String(plan.check.bonus) : '',
                        penalty: plan.check?.penalty ? String(plan.check.penalty) : '',
                      })}
                    >
                      填入
                    </button>
                    <button className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('dice_check', plan.check || {}, '已按建议发起检定')}>照此发起</button>
                  </div>
                )}
                {/* NPC 反应此前是纯文本，没有任何采纳路径：KP 得记住内容、切回主持、
                    重选 NPC、把台词手抄一遍。 */}
                {plan.npc_policy?.reaction && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>NPC 反应：{plan.npc_policy.reaction}</span>
                    <button
                      className="btn-secondary !px-2 !py-0.5"
                      title="填入主持表单的 NPC 台词，可改写后再发布"
                      onClick={() => adoptToTools('dialogue', {
                        npc_id: plan.npc_policy?.speakers?.[0],
                        content: plan.npc_policy?.reaction,
                      })}
                    >
                      填入台词
                    </button>
                  </div>
                )}
                {plan.auto_outcome && plan.auto_outcome !== 'none' && <div>免检结论：{plan.auto_outcome} · {plan.auto_outcome_reason}</div>}
                {!!plan.clue_policy?.candidate_clue_ids?.length && <div>候选线索：{plan.clue_policy.candidate_clue_ids.join('、')} · {plan.clue_policy.notes}</div>}
                {plan.scene_policy?.scene_change && (
                  <div className="flex items-center gap-2">建议切场：{plan.scene_policy.scene_change}<button className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('scene_change', { scene_id: plan.scene_policy?.scene_change }, '已按建议切换场景')}>执行</button></div>
                )}
                {!!plan.scene_policy?.set_flags?.length && <div className="flex flex-wrap items-center gap-2">建议推进标志：{plan.scene_policy.set_flags.map((flag) => <button key={flag} className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('set_flag', { flag }, `已推进标志 ${flag}`)}>{flag}</button>)}</div>}
                {!!plan.scene_policy?.clear_flags?.length && <div className="flex flex-wrap items-center gap-2">建议解除标志：{plan.scene_policy.clear_flags.map((flag) => <button key={flag} className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('clear_flag', { flag }, `已解除标志 ${flag}`)}>{flag}</button>)}</div>}
                {plan.sanity?.trigger && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>建议理智检定：{plan.sanity.source}</span>
                    <button
                      className="btn-secondary !px-2 !py-0.5"
                      title="填入主持表单，可改目睹者/损失值后再执行"
                      onClick={() => adoptToTools('san_check', {
                        chars: plan.sanity?.witnesses?.join('、'),
                        source: plan.sanity?.source,
                        success_loss: plan.sanity?.success_loss,
                        failure_loss: plan.sanity?.failure_loss,
                      })}
                    >
                      填入
                    </button>
                    <button className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('san_check', { chars: plan.sanity?.witnesses?.join('、'), source: plan.sanity?.source, success_loss: plan.sanity?.success_loss, failure_loss: plan.sanity?.failure_loss }, '已按建议进行理智检定')}>执行</button>
                  </div>
                )}
                {plan.combat?.should_start && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>建议开战：{plan.combat.enemies?.join('、')}</span>
                    <button
                      className="btn-secondary !px-2 !py-0.5"
                      title="填入主持表单，可增删参战敌人后再开战"
                      onClick={() => adoptToTools('start_combat', {
                        // 开战表单的 enemies 是逗号分隔（MultiSelect 按 , 切分），与理智检定的顿号不同
                        enemies: plan.combat?.enemies?.join(','),
                        trigger: plan.combat?.trigger,
                      })}
                    >
                      填入
                    </button>
                    <button className="btn-secondary !px-2 !py-0.5" onClick={() => void postAction('start_combat', { enemies: plan.combat?.enemies?.join('、'), trigger: plan.combat?.trigger }, '已按建议开始战斗')}>执行</button>
                  </div>
                )}
                {/* 导演建议同样是纯文本 —— 补一条「填入叙事」，省去记忆+手抄 */}
                {(plan.direction?.nudge || plan.direction?.foreshadow) && (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>导演建议：{plan.direction.nudge || plan.direction.foreshadow}</span>
                    <button
                      className="btn-secondary !px-2 !py-0.5"
                      title="填入主持表单的旁白，可改写后再发布"
                      onClick={() => adoptToTools('narration', {
                        content: plan.direction?.nudge || plan.direction?.foreshadow,
                      })}
                    >
                      填入叙事
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="flex flex-wrap gap-2 border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
            <input value={draftInstruction} onChange={(event) => setDraftInstruction(event.target.value)} placeholder="希望草稿呈现的内容、语气或长度" className="input min-w-48 flex-1 text-xs" />
            <button onClick={() => void generateDraft()} disabled={!!busy} className="btn-secondary inline-flex items-center gap-1 text-xs">
              <WandSparkles size={13} /> {busy === 'draft' ? '起草中…' : '生成叙事草稿'}
            </button>
            <AdvisorHelp
              label="叙事草稿"
              text="根据当前游戏上下文生成仅 KP 可见、可继续编辑的候选旁白。生成草稿不会推进游戏；确认并点击“发布草稿”后，内容才会展示给玩家。"
            />
            {draft && <>
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)} className="input min-h-24 w-full resize-y text-xs" />
              <button onClick={() => void postAction('narration', { content: draft }, '叙事草稿已发布')} className="btn-primary inline-flex items-center gap-1 text-xs"><Send size={13} />发布草稿</button>
            </>}
          </div>

          <div className="flex flex-wrap gap-2">
            <div className="inline-flex items-center gap-1 rounded border p-0.5" style={{ borderColor: 'var(--color-border)' }} role="tablist" aria-label="检索范围">
              {(['module', 'rule'] as const).map((scope) => (
                <button
                  key={scope}
                  type="button"
                  onClick={() => { setLookupScope(scope); setLookupHits([]); setLookupOpen(false) }}
                  className="btn-secondary !border-0 !px-2 !py-1 text-xs"
                  style={lookupScope === scope ? { background: 'var(--color-bg-tertiary)', color: 'var(--color-text-accent)' } : undefined}
                  role="tab"
                  aria-selected={lookupScope === scope}
                >
                  {scope === 'module' ? '模组原文' : '规则书'}
                </button>
              ))}
            </div>
            <input value={lookupQuery} onChange={(event) => setLookupQuery(event.target.value)} placeholder="输入检索关键词" className="input min-w-48 flex-1 text-xs" />
            <button onClick={() => void runLookup()} disabled={!!busy} className="btn-secondary inline-flex items-center gap-1 text-xs"><BookOpen size={13} />{busy === 'lookup' ? '检索中…' : '速查'}</button>
            {!!lookupHits.length && <button type="button" onClick={() => setLookupOpen(true)} className="btn-secondary !px-2 !py-1 text-xs">查看 {lookupHits.length} 条结果</button>}
          </div>
        </div>
      )}

      {!collapsed && tab === 'assets' && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
            <label className="inline-flex items-center gap-2 text-xs">
              <input type="checkbox" checked={workspace?.auto_ai_teammates || false} disabled={!workspace?.has_ai_teammates || !!busy} onChange={(event) => void updateAutoTeammates(event.target.checked)} />
              玩家提交后让 AI 队友自动行动
            </label>
            <button onClick={() => void runTeamTurn()} disabled={!workspace?.has_ai_teammates || !workspace?.has_unprocessed_player_turn || !!busy} className="btn-secondary inline-flex items-center gap-1 text-xs">
              <Bot size={13} /> {busy === 'team' ? '行动中…' : '让 AI 队友行动'}
            </button>
          </div>
          {!!workspace?.image_suggestions?.length && (
            <div className="space-y-2 border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
              <div className="flex items-center gap-1 text-xs font-semibold" style={{ color: 'var(--color-text-primary)' }}>
                <Image size={13} /> 待审核配图
              </div>
              <div className="space-y-2">
                {workspace.image_suggestions.map((suggestion) => (
                  <div key={suggestion.key} className="flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                    {suggestion.preview_url && <img src={imageUrl(suggestion.preview_url)} alt="" className="h-10 w-14 rounded object-cover" />}
                    <span className="min-w-0 flex-1">{suggestion.title}</span>
                    <button type="button" onClick={() => void generateImage(suggestion)} disabled={!!busy} className="btn-secondary inline-flex items-center gap-1 !px-2 !py-1 text-xs">
                      <Image size={12} /> {suggestion.preview_url ? '采用缓存预览' : '生成预览'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <input value={imageTitle} onChange={(event) => setImageTitle(event.target.value)} placeholder="配图标题" className="input w-36 text-xs" />
            <input value={imagePrompt} onChange={(event) => setImagePrompt(event.target.value)} placeholder="描述希望生成的画面" className="input min-w-48 flex-1 text-xs" />
            <button onClick={() => void generateImage()} disabled={!!busy || !imagePrompt.trim()} className="btn-secondary inline-flex items-center gap-1 text-xs"><Image size={13} />{busy === 'image' ? '生成中…' : '生成预览'}</button>
            {previewUrl && <div className="w-full"><img src={imageUrl(previewUrl)} alt={imageTitle} className="max-h-64 w-full rounded object-contain" /><div className="mt-2 flex justify-end"><button onClick={() => void publishImage()} disabled={!!busy} className="btn-primary inline-flex items-center gap-1 text-xs"><Send size={13} />发布给全桌</button></div></div>}
          </div>
        </div>
      )}

      {!collapsed && tab === 'director' && (
        <div className="space-y-3">
          <div className="flex items-start gap-2 border-b pb-3 text-xs" style={{ borderColor: 'var(--color-border)' }}>
            <div className="min-w-0 flex-1 space-y-1" style={{ color: 'var(--color-text-secondary)' }}>
              {!!signals?.spotlight_starved.length && <div>冷场角色：{signals.spotlight_starved.join('、')}</div>}
              {signals?.stuck && <div>卡关迹象：约 {signals.stuck_turns} 个玩家回合没有实质进展</div>}
              {signals?.monotonous && <div>节奏单调：近期调查动作密集，可加入人物互动或情绪换气</div>}
              {signals?.ending_ready && (
                <div style={{ color: 'var(--color-success)' }}>
                  主线已走完（该触发的都触发了、线索也基本掌握）：适合收束并提示玩家结束本模组——
                  结束与否由玩家投票决定，你不必代为宣布。
                </div>
              )}
              {!!signals?.promotable_npcs?.length && (
                <div>
                  已站住脚的临场角色：{signals.promotable_npcs.join('、')}
                  ——可在顶栏「临场角色」里收编为正式配角，之后他们才有稳定人设与记忆。
                </div>
              )}
              {!!signals?.unresolved_threads.length && <div>待回收：{signals.unresolved_threads.join('；')}</div>}
              {!signals?.spotlight_starved.length && !signals?.stuck && !signals?.monotonous && !signals?.unresolved_threads.length && <div>当前没有明显的节奏风险。</div>}
            </div>
            <button onClick={() => void refreshWorkspace()} className="btn-secondary !p-1.5" title="刷新导演信号"><RefreshCw size={13} /></button>
          </div>
          <textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="记录尚未公开的秘密、临场裁定与后续安排" className="input min-h-28 w-full resize-y text-xs" />
          <button onClick={() => void saveNotes()} disabled={!!busy} className="btn-primary inline-flex items-center gap-1 text-xs"><NotebookPen size={13} />{busy === 'notes' ? '保存中…' : '保存私有笔记'}</button>
        </div>
      )}

      {!collapsed && <>
      </>}

      {sourceOpen && !collapsed && (
        <aside
          className="fixed right-4 top-20 z-50 flex max-h-[calc(100vh-5rem)] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-md border shadow-xl backdrop-blur-md"
          style={{
            background: 'color-mix(in srgb, var(--color-bg-secondary) 88%, transparent)',
            borderColor: 'var(--color-border-strong)',
            width: `${sourceSize.width}px`,
            height: `${sourceSize.height}px`,
            transform: `translate3d(${sourcePosition.x}px, ${sourcePosition.y}px, 0)`,
          }}
          aria-label="KP 模组资料"
        >
          <div
            className="flex cursor-move touch-none items-center gap-2 border-b px-3 py-2"
            style={{ borderColor: 'var(--color-border)' }}
            {...sourceDrag}
            onPointerCancel={sourceDrag.onPointerUp}
            title="拖动资料窗"
          >
            <Move size={14} style={{ color: 'var(--color-text-secondary)' }} />
            <BookOpen size={15} style={{ color: 'var(--color-text-accent)' }} />
            <span className="min-w-0 flex-1 truncate text-sm font-semibold">{source?.title || '模组资料'}</span>
            <button type="button" onClick={() => void openModuleSource(true)} disabled={busy === 'source'} className="btn-secondary !p-1.5" title="刷新模组资料">
              <RefreshCw size={13} className={busy === 'source' ? 'animate-spin' : ''} />
            </button>
            <button type="button" onClick={() => setSourceOpen(false)} className="btn-secondary !p-1.5" title="关闭模组资料">
              <X size={15} />
            </button>
          </div>
          <div className="flex items-center gap-1 border-b px-3 py-2" style={{ borderColor: 'var(--color-border)' }} role="tablist" aria-label="资料视图">
            <button type="button" onClick={() => setSourceView('raw')} className="btn-secondary !px-2 !py-1 text-xs" style={sourceView === 'raw' ? { color: 'var(--color-text-accent)' } : undefined} role="tab" aria-selected={sourceView === 'raw'}>原文</button>
            <button type="button" onClick={() => setSourceView('parsed')} className="btn-secondary !px-2 !py-1 text-xs" style={sourceView === 'parsed' ? { color: 'var(--color-text-accent)' } : undefined} role="tab" aria-selected={sourceView === 'parsed'}>解析内容</button>
            <span className="ml-auto text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>仅 KP 可见</span>
          </div>
          <div className="flex items-center gap-2 border-b px-3 py-2" style={{ borderColor: 'var(--color-border)' }}>
            <Search size={14} style={{ color: 'var(--color-text-secondary)' }} />
            <input
              value={sourceQuery}
              onChange={(event) => { setSourceQuery(event.target.value); setSourceTargetOrdinal(null) }}
              placeholder="搜索原文或解析内容"
              className="input min-w-0 flex-1 text-xs"
              aria-label="搜索模组资料"
            />
            {sourceQuery && <button type="button" onClick={() => { setSourceQuery(''); setSourceTargetOrdinal(null) }} className="btn-secondary !p-1.5" title="清除搜索"><X size={13} /></button>}
            <span className="shrink-0 text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>
              {sourceView === 'parsed' && sourceChunks.length ? `${visibleSourceChunks.length}/${sourceChunks.length} 段` : '全文'}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {!source ? (
              <div className="py-12 text-center text-sm" style={{ color: 'var(--color-text-secondary)' }}>正在加载模组资料…</div>
            ) : sourceView === 'raw' ? (
              <pre className="whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--color-text-primary)' }}>{highlightText(source.raw_content || source.description || '该模组没有保存原文。', sourceQuery)}</pre>
            ) : (
              <div className="space-y-3">
                {sourceChunks.length > 0 && (
                  <section className="border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
                    <div className="mb-1 flex items-center gap-2">
                      <h4 className="min-w-0 flex-1 text-xs font-semibold" style={{ color: 'var(--color-text-accent)' }}>原文分段</h4>
                      <span className="text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>按检索结果定位</span>
                    </div>
                    {visibleSourceChunks.length ? visibleSourceChunks.map((chunk) => (
                      <div
                        key={chunk.ordinal}
                        ref={(element) => { sourceChunkRefs.current[chunk.ordinal] = element }}
                        className="mb-2 rounded border p-2 last:mb-0"
                        style={{
                          borderColor: chunk.ordinal === sourceTargetOrdinal ? 'var(--color-accent)' : 'var(--color-border)',
                          background: chunk.ordinal === sourceTargetOrdinal ? 'color-mix(in srgb, var(--color-accent) 12%, transparent)' : 'transparent',
                        }}
                      >
                        <div className="mb-1 text-[11px]" style={{ color: 'var(--color-text-secondary)' }}>第 {chunk.ordinal + 1} 段{chunk.scene_hint ? ` · ${sceneLabel(chunk.scene_hint)}` : ''}</div>
                        <pre className="whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--color-text-primary)' }}>{highlightText(chunk.text, sourceQuery)}</pre>
                      </div>
                    )) : <div className="py-4 text-center text-xs" style={{ color: 'var(--color-text-secondary)' }}>没有匹配的原文分段。</div>}
                  </section>
                )}
                {sourceSections.map(([label, value]) => {
                  const text = typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)
                  if (!text || text === '{}' || text === '[]') return null
                  if (normalizedSourceQuery && !text.toLocaleLowerCase().includes(normalizedSourceQuery)) return null
                  return (
                    <section key={label} className="border-b pb-3" style={{ borderColor: 'var(--color-border)' }}>
                      <div className="mb-1 flex items-center gap-2">
                        <h4 className="min-w-0 flex-1 text-xs font-semibold" style={{ color: 'var(--color-text-accent)' }}>{label}</h4>
                        <button
                          type="button"
                          onClick={() => {
                            setImagePrompt(text.slice(0, 1500))
                            setTab('assets')
                            setSourceOpen(false)
                          }}
                          className="btn-secondary inline-flex items-center gap-1 !px-1.5 !py-0.5 text-[11px]"
                          title="将该段内容带入配图提示"
                        >
                          <Image size={11} /> 用于配图
                        </button>
                      </div>
                      <pre className="whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--color-text-primary)' }}>{highlightText(text, sourceQuery)}</pre>
                    </section>
                  )
                })}
              </div>
            )}
          </div>
          <div
            className="absolute bottom-1 right-1 flex h-5 w-5 cursor-se-resize touch-none items-end justify-end"
            {...sourceResize}
            onPointerCancel={sourceResize.onPointerUp}
            title="调整资料窗大小"
            aria-label="调整资料窗大小"
          >
            <Maximize2 size={13} style={{ color: 'var(--color-text-secondary)' }} />
          </div>
        </aside>
      )}

      {lookupOpen && !collapsed && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/45 p-4 pt-16"
          role="dialog"
          aria-modal="true"
          aria-label={`${lookupScope === 'module' ? '模组原文' : '规则书'}检索结果`}
          onMouseDown={(event) => { if (event.button === 0 && event.target === event.currentTarget) setLookupOpen(false) }}
        >
          <div className="w-full max-w-3xl overflow-hidden rounded-md" style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border-strong)', boxShadow: '0 6px 18px var(--shadow-color-strong)' }}>
            <div className="flex items-center gap-2 border-b px-4 py-3" style={{ borderColor: 'var(--color-border)' }}>
              <BookOpen size={16} style={{ color: 'var(--color-text-accent)' }} />
              <div className="min-w-0 flex-1 text-sm font-semibold">{lookupScope === 'module' ? '模组原文' : '规则书'}检索结果</div>
              <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>{lookupHits.length} 条</span>
              <button type="button" onClick={() => setLookupOpen(false)} className="btn-secondary !p-1.5" title="关闭检索结果"><X size={15} /></button>
            </div>
            <div className="max-h-[70vh] space-y-3 overflow-y-auto p-4">
              {lookupHits.length ? lookupHits.map((hit, index) => (
                <article key={`${hit.page || hit.scene_hint || hit.ordinal || ''}-${index}`} className="border-l-2 pl-3 text-xs" style={{ borderColor: 'var(--color-border-strong)', color: 'var(--color-text-secondary)' }}>
                  <div className="mb-1 flex items-center gap-2 font-semibold" style={{ color: 'var(--color-text-primary)' }}>
                    {lookupScope === 'rule' ? (hit.page ? `规则书第 ${hit.page} 页` : '规则书片段') : sceneLabel(hit.scene_hint)}
                    {typeof hit.score === 'number' && <span className="ml-2 font-normal opacity-70">相关度 {hit.score.toFixed(3)}</span>}
                    {lookupScope === 'module' && <button type="button" onClick={() => void openModuleAtHit(hit)} className="btn-secondary ml-auto inline-flex shrink-0 items-center gap-1 !px-1.5 !py-0.5 text-[11px]" title="在模组资料窗定位"><Crosshair size={11} />定位到资料</button>}
                  </div>
                  <div className="whitespace-pre-wrap leading-6">{hit.text}</div>
                </article>
              )) : (
                <div className="py-10 text-center text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                  暂无匹配结果。{lookupScope === 'rule' && '规则书检索需要先在规则书管理中完成索引。'}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      </div>
      </div>
    </section>
  )
}
