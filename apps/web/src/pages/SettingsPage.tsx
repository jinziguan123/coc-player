import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { AlertTriangle, Copy, RefreshCw } from 'lucide-react'
import { GiArtificialIntelligence, GiNetworkBars, GiPaintBrush, GiArchiveResearch } from 'react-icons/gi'
import { toast } from 'sonner'
import { api, localApi } from '../api/client'
import { Switch } from '../components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  THEMES, getTheme, setTheme, getSceneBackdropEnabled, setSceneBackdropEnabled, type Theme,
} from '@/lib/theme'
import { useLocation, useNavigate } from 'react-router-dom'
import { getOnboardingReturnTo } from '@/features/onboarding/navigation'
import { AISettingsPanel } from '@/features/settings/AISettingsPanel'
import { LanRosterPanel } from '@/features/settings/LanRosterPanel'
import { NetlinkPanel } from '@/features/settings/NetlinkPanel'

/* ---------- 二级导航项 ---------- */

const SETTINGS_TABS = [
  { key: 'ai', label: 'AI 配置', Icon: GiArtificialIntelligence },
  { key: 'network', label: '联机', Icon: GiNetworkBars },
  { key: 'appearance', label: '外观', Icon: GiPaintBrush },
  { key: 'rag', label: 'RAG 统计', Icon: GiArchiveResearch },
  // 未来扩展：{ key: 'game', label: '游戏设置' },
] as const

type SettingsTab = (typeof SETTINGS_TABS)[number]['key']

/* ---------- 组件 ---------- */

export function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>('ai')
  const location = useLocation()
  const navigate = useNavigate()
  const returnTo = getOnboardingReturnTo(location.state)

  return (
    <div style={{ display: 'flex', gap: 0, height: '100%', minHeight: 0 }}>
      {/* 左侧二级导航：与主侧边栏同一套语言（静息灰字 / 选中琥珀 + 左缘色带） */}
      <nav className="subnav">
        <div className="subnav-title">设置</div>
        {SETTINGS_TABS.map(({ key, label, Icon }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`subnav-item ${activeTab === key ? 'active' : ''}`}
          >
            <Icon aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>

      {/* 右侧内容区 */}
      <div style={{ flex: 1, padding: '1rem 1.5rem', overflow: 'auto' }}>
        {activeTab === 'ai' && (
          <AISettingsPanel
            onTestSuccess={returnTo ? () => navigate(returnTo, { replace: true }) : undefined}
          />
        )}
        {activeTab === 'network' && <NetworkSettingsPanel />}
        {activeTab === 'appearance' && <AppearanceSettingsPanel />}
        {activeTab === 'rag' && <RagStatsPanel />}
      </div>
    </div>
  )
}

/* ---------- 联机面板 ---------- */

interface AIQuotaPolicy {
  enabled: boolean
  /** `limits` 库写法，如 "100/hour"、"20/minute"。 */
  limit: string
}

interface NetStatus {
  lan_enabled: boolean
  listening_on_lan: boolean
  restart_required: boolean
  addresses: string[]
  port: number | null
}

function NetworkSettingsPanel() {
  const [status, setStatus] = useState<NetStatus | null>(null)
  const [saving, setSaving] = useState(false)
  const [statusLoadFailed, setStatusLoadFailed] = useState(false)
  const [quota, setQuota] = useState<AIQuotaPolicy | null>(null)
  const [quotaSaving, setQuotaSaving] = useState(false)
  const [quotaLoadFailed, setQuotaLoadFailed] = useState(false)
  const [quotaLimitDraft, setQuotaLimitDraft] = useState('')

  const loadStatus = useCallback(async () => {
    setStatusLoadFailed(false)
    setStatus(null)
    try {
      setStatus(await localApi.get<NetStatus>('/net'))
    } catch {
      setStatusLoadFailed(true)
    }
  }, [])

  const loadQuota = useCallback(async () => {
    setQuotaLoadFailed(false)
    setQuota(null)
    try {
      const next = await localApi.get<AIQuotaPolicy>('/settings/ai/quota')
      setQuota(next)
      setQuotaLimitDraft(next.limit)
    } catch {
      setQuotaLoadFailed(true)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
    void loadQuota()
  }, [loadQuota, loadStatus])

  const saveQuota = async (next: Partial<AIQuotaPolicy>) => {
    if (!quota) return
    setQuotaSaving(true)
    try {
      const saved = await localApi.put<AIQuotaPolicy>('/settings/ai/quota', { ...quota, ...next })
      setQuota(saved)
      setQuotaLimitDraft(saved.limit)
      toast.success('AI 配额设置已保存')
    } catch {
      if (next.limit !== undefined) setQuotaLimitDraft(quota.limit)
      toast.error('保存失败')
    } finally {
      setQuotaSaving(false)
    }
  }

  const toggle = async (enabled: boolean) => {
    setSaving(true)
    try {
      setStatus(await localApi.post<NetStatus>('/net/lan', { enabled }))
      toast.success(enabled ? '已允许局域网加入' : '已关闭局域网加入')
    } catch {
      toast.error('设置失败')
    } finally {
      setSaving(false)
    }
  }

  const copyAddr = async (url: string) => {
    try {
      if (!navigator.clipboard) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(url)
      toast.success('地址已复制')
    } catch {
      toast.error('复制失败，请手动选择地址')
    }
  }

  const enabled = status?.lan_enabled ?? false
  // 桌面版端口是后端启动时挑的，由 /net 给出；开发态后端不知道自己被绑在哪个端口，
  // 回落到当前页面的端口（同源托管时二者一致）。
  //
  // 但 `pnpm tauri dev` 下页面是 vite（5173）、后端在 8000，两者不同源——此时回落到
  // 页面端口会让内置直连反代到 vite 而不是后端。dev 固定走 8000，见 vite.config.ts 的代理。
  const port =
    status?.port ?? (import.meta.env.DEV ? 8000 : Number(window.location.port) || null)
  const urlFor = (addr: string) => (port ? `http://${addr}:${port}` : `http://${addr}`)
  const statusText = statusLoadFailed
    ? '读取失败'
    : status === null
      ? '读取中'
      : enabled
        ? '已开启'
        : '已关闭'
  const quotaText = quotaLoadFailed
    ? '读取失败'
    : quota === null
      ? '读取中'
      : quota.enabled
        ? '已启用'
        : '未启用'

  return (
    <div className="network-settings">
      <h2 className="page-title">联机</h2>

      {/* 允许局域网加入 */}
      <div className={`card ${enabled ? 'active-rail' : ''}`}>
        <div className="setting-head">
          <h3 className="card-title" style={{ margin: 0 }}>
            允许局域网加入
          </h3>
          <Switch
            label="允许局域网加入"
            checked={enabled}
            disabled={saving || status === null}
            onChange={toggle}
            onText={statusText}
            offText={statusText}
          />
        </div>
        <p className="setting-description">
          关闭时后端只监听本机，同一网络内的其他设备也连不上——这是默认状态。
          打开后其他玩家可以在「加入房间」处填你的地址进来。
        </p>

        {statusLoadFailed && (
          <div className="notice notice--danger setting-retry" role="alert">
            <AlertTriangle size={13} aria-hidden="true" />
            <span>读取联机状态失败，当前无法确认是否允许其他设备加入。</span>
            <button
              type="button"
              className="icon-btn"
              onClick={() => void loadStatus()}
              title="重新读取联机状态"
              aria-label="重新读取联机状态"
            >
              <RefreshCw size={13} />
            </button>
          </div>
        )}

        {status?.restart_required && (
          <div className="notice" style={{ marginTop: '0.75rem' }}>
            <RefreshCw
              size={12}
              style={{ flexShrink: 0, marginTop: '0.15rem' }}
              aria-hidden="true"
            />
            <span>
              设置已保存，但监听地址在应用启动时确定——
              {enabled ? '需重启应用，其他玩家才能连进来。' : '重启前，本机之外的请求已经被拒绝。'}
            </span>
          </div>
        )}

        {status?.lan_enabled && (
          <div style={{ marginTop: '0.75rem' }}>
            <div
              className="text-xs"
              style={{ color: 'var(--color-text-secondary)', marginBottom: '0.4rem' }}
            >
              {status.addresses.length > 0
                ? '把地址连同房间码发给其他玩家（点击复制）：'
                : '没有找到可用的局域网地址——本机可能没连上网，或只连着会接管路由的 VPN。'}
            </div>
            <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
              {status.addresses.map((addr) => (
                <button
                  key={addr}
                  type="button"
                  onClick={() => void copyAddr(urlFor(addr))}
                  className="copy-line"
                  title="点击复制"
                  aria-label={`复制联机地址 ${urlFor(addr)}`}
                >
                  {urlFor(addr)}
                  <Copy size={11} style={{ opacity: 0.7 }} aria-hidden="true" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 谁能连进来。开关只回答「这个网段可不可信」，具体是哪台设备由名册逐个放行。 */}
        <LanRosterPanel lanEnabled={enabled} />
      </div>

      {/* 内置直连 */}
      <NetlinkPanel backendPort={port} />

      {/* 房间 AI 配额 */}
      <div className={`card ${quota?.enabled ? 'active-rail' : ''}`}>
        <div className="setting-head">
          <h3 className="card-title" style={{ margin: 0 }}>
            房间 AI 配额
          </h3>
          <Switch
            label="房间 AI 配额"
            checked={!!quota?.enabled}
            disabled={quotaSaving || quota === null}
            onChange={(next) => saveQuota({ enabled: next })}
            onText={quotaText}
            offText={quotaText}
          />
        </div>
        <p className="setting-description">
          房内玩家的正常动作（发言、投骰、推进回合）都会驱动 AI，烧的是你配置的额度。
          启用后每个房间在时间窗内能触发的生成次数受限，超出时该房间暂时无法推进。
          默认关闭——自己单机玩不该被限。
        </p>

        {quotaLoadFailed && (
          <div className="notice notice--danger setting-retry" role="alert">
            <AlertTriangle size={13} aria-hidden="true" />
            <span>读取 AI 配额失败，限制策略暂不可编辑。</span>
            <button
              type="button"
              className="icon-btn"
              onClick={() => void loadQuota()}
              title="重新读取 AI 配额"
              aria-label="重新读取 AI 配额"
            >
              <RefreshCw size={13} />
            </button>
          </div>
        )}

        {quota?.enabled && (
          <label className="quota-editor">
            <span>每房间上限</span>
            <input
              className="input"
              aria-label="每房间 AI 配额上限"
              value={quotaLimitDraft}
              onChange={(event) => setQuotaLimitDraft(event.target.value)}
              onBlur={() => {
                const value = quotaLimitDraft.trim()
                if (!value) {
                  setQuotaLimitDraft(quota.limit)
                } else if (value !== quota.limit) {
                  void saveQuota({ limit: value })
                }
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur()
                if (event.key === 'Escape') {
                  setQuotaLimitDraft(quota.limit)
                  event.currentTarget.blur()
                }
              }}
              disabled={quotaSaving}
            />
            <span className="quota-hint">如 100/hour、20/minute</span>
          </label>
        )}
      </div>

      {/* 风险提示：用血色左带与正常设置卡区分开 */}
      <div className="card setting-risk-card">
        <h3
          className="card-title"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.35rem',
            color: 'var(--color-danger)',
          }}
        >
          <AlertTriangle size={13} aria-hidden="true" />
          开启前请确认
        </h3>
        <ul
          className="text-xs"
          style={{
            color: 'var(--color-text-secondary)', lineHeight: 1.8,
            paddingLeft: '1.1rem', margin: 0,
          }}
        >
          {/* 有了接入名册之后这条必须改口：同网段的人不再是「知道地址就能进」了。
              但也别说过头——名册按 token 认人、不验人，仍然没有账号体系和传输加密。 */}
          <li>
            陌生设备连进来时会先排在上面等你同意，同意过的下次直接进。但这只是「认人」
            不是「验人」：本应用没有账号体系，也没有传输加密，同一网络里有心伪造的人
            仍然可能冒充已获准的设备，读写房间内容、消耗你配置的 AI 额度。
          </li>
          <li>只在可信网络开启：家里或朋友家的 Wi-Fi。公共 Wi-Fi、酒店、公司网络都不要开。</li>
          <li>
            不要把这个端口转发到公网。即使转发了，来自互联网的请求也会被拒绝，
            但这只是兜底，不是可以依赖的防护。
          </li>
          <li>
            想和不在同一网络的朋友一起玩，优先用上面的「内置直连」；也可以用 Tailscale
            这类覆盖网络把双方接进同一个虚拟内网，而不是暴露端口——它们的地址段本应用已经放行。
          </li>
        </ul>
      </div>
    </div>
  )
}


/* ---------- 外观 / 主题面板 ---------- */

function AppearanceSettingsPanel() {
  const [theme, setThemeState] = useState<Theme>(() => getTheme())
  const [backdrop, setBackdrop] = useState(() => getSceneBackdropEnabled())

  const choose = (t: Theme) => {
    setTheme(t) // 写 localStorage + 改 documentElement.dataset.theme，即时生效
    setThemeState(t)
  }

  return (
    <div>
      <h2 className="page-title">外观</h2>
      <div className="card">
        <h3 className="card-title">主题</h3>
        <p
          className="text-xs"
          style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}
        >
          切换即时生效，刷新后保持。
        </p>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          {THEMES.map((opt) => {
            const active = theme === opt.value
            return (
              <button
                key={opt.value}
                onClick={() => choose(opt.value)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.5rem',
                  padding: '0.75rem',
                  minWidth: '9rem',
                  textAlign: 'left',
                  cursor: 'pointer',
                  borderRadius: '4px',
                  border: `1px solid ${active ? 'var(--color-accent)' : 'var(--color-border-strong)'}`,
                  background: active
                    ? 'rgba(212, 162, 78, 0.08)'
                    : 'var(--color-input-bg)',
                  transition: 'border-color 0.2s',
                }}
              >
                {/* 色板预览 */}
                <div style={{ display: 'flex', gap: '4px' }}>
                  {opt.swatch.map((c) => (
                    <span
                      key={c}
                      style={{
                        width: 22,
                        height: 22,
                        borderRadius: '3px',
                        background: c,
                        border: '1px solid rgba(128,128,128,0.35)',
                      }}
                    />
                  ))}
                </div>
                <span
                  style={{
                    fontFamily: 'var(--font-title)',
                    fontSize: '0.9rem',
                    fontWeight: 600,
                    color: active
                      ? 'var(--color-text-accent)'
                      : 'var(--color-text-primary)',
                  }}
                >
                  {opt.label}
                </span>
                <span
                  style={{
                    fontSize: '0.72rem',
                    color: 'var(--color-text-secondary)',
                  }}
                >
                  {active ? '当前使用' : '点击切换'}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">场景氛围底</h3>
        <p
          className="text-xs"
          style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}
        >
          把当前场景的配图重度模糊后铺在对局界面后面，只留下这个地方的色调（雾港的冷灰、
          地窖的暗褐），换场景时跟着变。图会被糊掉细节并压暗，不影响读字。
          没有配图的场景（未配置生图模型，或图还在生成）自动回落到主题底色。
        </p>
        <label
          style={{
            display: 'flex', alignItems: 'center', gap: '0.6rem',
            cursor: 'pointer', fontSize: '0.85rem',
          }}
        >
          <input
            type="checkbox"
            checked={backdrop}
            onChange={(e) => {
              setSceneBackdropEnabled(e.target.checked)
              setBackdrop(e.target.checked)
            }}
          />
          <span>在对局界面渲染场景氛围底</span>
        </label>
      </div>
    </div>
  )
}

/* ---------- RAG 统计面板 ---------- */

interface SessionListItem {
  id: string
  module_title: string | null
  character_name: string | null
  status?: string
}

interface RagQuadrant {
  calls: number
  empty: number
  total_hits: number
  hit_rate: number
  avg_top_score: number
}

interface RagSample {
  kind: string
  mode: string
  query: string
  n_hits: number
  top_score: number
}

interface RagStats {
  totals: { calls: number; total_hits: number; empty: number; hit_rate: number }
  by_kind_mode: Record<string, RagQuadrant>
  recent: RagSample[]
}

// 四象限固定顺序与中文标签（kind:mode）
const RAG_QUADRANTS: { key: string; label: string }[] = [
  { key: 'rule:active', label: '规则书 · 主动查阅' },
  { key: 'rule:passive', label: '规则书 · 被动注入' },
  { key: 'module:active', label: '模组原文 · 主动查阅' },
  { key: 'module:passive', label: '模组原文 · 被动注入' },
]

const pct = (x: number) => `${Math.round((x || 0) * 100)}%`
const sessionLabel = (s: SessionListItem) =>
  [s.module_title || '未命名模组', s.character_name || '—'].join(' · ')

function RagStatsPanel() {
  const [sessions, setSessions] = useState<SessionListItem[]>([])
  const [selected, setSelected] = useState<string>('')
  const [stats, setStats] = useState<RagStats | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get<SessionListItem[]>('/sessions')
      .then((list) => {
        setSessions(list)
        if (list.length && !selected) setSelected(list[0].id)
      })
      .catch(() => {})
    // 仅首次拉会话列表
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = useCallback((sid: string) => {
    if (!sid) return
    setLoading(true)
    api.get<RagStats>(`/sessions/${sid}/rag-stats`)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (selected) load(selected)
    else setStats(null)
  }, [selected, load])

  const t = stats?.totals
  const empty = !t || t.calls === 0

  return (
    <div>
      <h2 className="page-title">RAG 统计</h2>
      <div className="card">
        <h3 className="card-title">检索用量与命中质量</h3>
        <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem' }}>
          按局统计规则书 / 模组原文检索（RAG）的调用次数与命中质量，判断这套检索对跑团的实际帮助。
          <br />
          主动＝KP 发起的查阅；被动＝建上下文时按情境预取。命中率低 / 空命中多，说明语料覆盖或检索组织有待改进。
        </p>

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '1rem' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <Select value={selected} onValueChange={setSelected}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={sessions.length ? '选择一局游戏' : '暂无游戏'} />
              </SelectTrigger>
              <SelectContent>
                {sessions.map((s) => (
                  <SelectItem key={s.id} value={s.id}>{sessionLabel(s)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <button
            className="btn-secondary text-xs"
            onClick={() => selected && load(selected)}
            disabled={!selected || loading}
            style={{ flexShrink: 0 }}
          >
            {loading ? '刷新中…' : '刷新'}
          </button>
        </div>

        {!selected ? (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>请选择一局游戏查看。</p>
        ) : empty ? (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            本局尚无 RAG 调用记录（未挂规则书/模组原文索引，或还没跑过需要检索的回合）。
          </p>
        ) : (
          <>
            {/* 总计 */}
            <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
              <Stat label="总检索" value={String(t!.calls)} />
              <Stat label="命中率" value={pct(t!.hit_rate)} />
              <Stat label="命中片段" value={String(t!.total_hits)} />
              <Stat label="空命中" value={String(t!.empty)} />
            </div>

            {/* 四象限 */}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                <thead>
                  <tr style={{ color: 'var(--color-text-secondary)', textAlign: 'left' }}>
                    <th style={ragTh}>类别</th>
                    <th style={ragTh}>调用</th>
                    <th style={ragTh}>命中率</th>
                    <th style={ragTh}>空命中</th>
                    <th style={ragTh}>平均 top 分</th>
                  </tr>
                </thead>
                <tbody>
                  {RAG_QUADRANTS.map(({ key, label }) => {
                    const q = stats!.by_kind_mode[key]
                    return (
                      <tr key={key} style={{ borderTop: '1px solid var(--color-border)' }}>
                        <td style={ragTd}>{label}</td>
                        <td style={ragTd}>{q?.calls ?? 0}</td>
                        <td style={ragTd}>{q ? pct(q.hit_rate) : '—'}</td>
                        <td style={ragTd}>{q?.empty ?? 0}</td>
                        <td style={ragTd}>{q?.avg_top_score != null ? q.avg_top_score.toFixed(3) : '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* 最近样本 */}
            {stats!.recent.length > 0 && (
              <div style={{ marginTop: '1.25rem' }}>
                <div className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.5rem' }}>
                  最近 {stats!.recent.length} 次检索
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                  {stats!.recent.map((r, i) => (
                    <div
                      key={i}
                      style={{
                        display: 'flex', gap: '0.6rem', alignItems: 'baseline',
                        fontSize: '0.78rem', color: 'var(--color-text-primary)',
                      }}
                    >
                      <span style={{ color: 'var(--color-text-secondary)', flexShrink: 0, width: '9.5rem' }}>
                        {RAG_QUADRANTS.find((x) => x.key === `${r.kind}:${r.mode}`)?.label
                          ?? `${r.kind}:${r.mode}`}
                      </span>
                      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.query || '（空 query）'}
                      </span>
                      <span style={{
                        flexShrink: 0,
                        color: r.n_hits ? 'var(--color-text-secondary)' : 'var(--color-accent)',
                      }}>
                        {r.n_hits ? `${r.n_hits} 命中 · ${r.top_score.toFixed(3)}` : '未命中'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

const ragTh: CSSProperties = { padding: '0.4rem 0.6rem', fontWeight: 600 }
const ragTd: CSSProperties = { padding: '0.4rem 0.6rem' }

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem' }}>
      <span style={{ fontSize: '0.72rem', color: 'var(--color-text-secondary)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-title)', fontSize: '1.15rem', color: 'var(--color-text-primary)' }}>
        {value}
      </span>
    </div>
  )
}
