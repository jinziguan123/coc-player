/**
 * 主持写作台的输入区：按当前动作换形态（旁白 / 台词 / 检定 / 开战 …）。
 *
 * 从 HumanKpPanel 拆出：这一段只读 fields、只经回调写回，不碰面板的其他状态。
 * 提交逻辑（字段 → payload）仍留在面板的 submit 里，这里只负责「长什么样」。
 */
import type { KeyboardEvent, ReactNode } from 'react'
import { GiFootTrip, GiPadlock, GiRun, GiScrollUnfurled, GiSkullCrossedBones } from 'react-icons/gi'
import { Mic, MicOff, Swords } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { MultiSelect } from '@/components/ui/multi-select'
import { KP_MOOD_OPTIONS } from '@/features/game-session/bgm'
import { KpInput } from './KpInput'
import { HpChangeForm, MarkSeenForm, NpcActForm, PlaceReasonForm } from './KpExtraForms'
import { catalogOptions } from './kpCatalogs'
import type { KpAction } from './kpActions'

interface CatalogItem {
  id: string
  name: string
}

/** 只认 catalogs 的形状，不绑 HumanKpPanel 的 Workspace 类型。 */
interface Workspace {
  catalogs: {
    characters: CatalogItem[]
    scenes: CatalogItem[]
    npcs: CatalogItem[]
    handouts: CatalogItem[]
    clues?: CatalogItem[]
  }
}

interface ActorSelectProps {
  label: string
  value: string
  workspace: Workspace | null
  onChange: (value: string) => void
  includeGroup?: boolean
  disabledValue?: string
}

function ActorSelect({
  label, value, workspace, onChange, includeGroup = false, disabledValue,
}: ActorSelectProps) {
  const emptyValue = '__kp_no_actor__'
  return (
    <Select value={value || emptyValue} onValueChange={(nextValue) => onChange(nextValue === emptyValue ? '' : nextValue)}>
      <SelectTrigger className="min-w-40 flex-1 text-xs" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={emptyValue}>选择{label}</SelectItem>
        {includeGroup && <SelectItem value="在场">在场所有角色</SelectItem>}
        {!!workspace?.catalogs.characters?.length && (
          <SelectGroup>
            <SelectLabel>游戏角色</SelectLabel>
            {workspace.catalogs.characters.map((item) => {
              const ref = `character:${item.id}`
              return <SelectItem key={ref} value={ref} disabled={ref === disabledValue}>{item.name}</SelectItem>
            })}
          </SelectGroup>
        )}
        {!!workspace?.catalogs.npcs.length && (
          <SelectGroup>
            <SelectLabel>NPC</SelectLabel>
            {workspace.catalogs.npcs.map((item) => {
              const ref = `npc:${item.id}`
              return <SelectItem key={ref} value={ref} disabled={ref === disabledValue}>{item.name}</SelectItem>
            })}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  )
}

/**
 * 封闭清单（场景 / 手书 / 配乐氛围）的下拉：看到的是名字，提交的是 id。
 *
 * 这几样从前是可输入的候选框，候选项把 id 摆在前面、名字作旁注——KP 要么自己敲
 * 「scene_3」「handout_2」这种内部编号，要么在一排编号里找名字。它们本来就是模组里
 * 有限的几项，没有临场乱填的余地，直接按名字挑就对了。
 */
function NamedSelect({
  label, value, items, onChange, empty,
}: {
  label: string
  value: string
  items: Array<{ id: string; name: string }>
  onChange: (value: string) => void
  /** 一项都没有时显示的话（替代一个空下拉） */
  empty: string
}) {
  if (!items.length) {
    return <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>{empty}</span>
  }
  // 参谋「采纳到写作台」可能填进来的是名字而不是 id：清单里认不出就原样挂一项，别显示成空
  const shown = value && !items.some((i) => i.id === value) ? [...items, { id: value, name: value }] : items
  return (
    <Select value={value || undefined} onValueChange={onChange}>
      <SelectTrigger className="min-w-48 flex-1 text-xs" aria-label={label}>
        <SelectValue placeholder={`选择${label}`} />
      </SelectTrigger>
      <SelectContent>
        {shown.map((item) => <SelectItem key={item.id} value={item.id}>{item.name || item.id}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}

/** 配乐氛围：名字是给人看的中文，提交的仍是氛围键 */
const MOOD_ITEMS = KP_MOOD_OPTIONS.map((o) => ({ id: o.value, name: o.hint }))

function DiceAdjustment({
  label, bonus, penalty, onChange,
}: { label: string; bonus: string; penalty: string; onChange: (bonus: string, penalty: string) => void }) {
  const value = bonus && bonus !== '0' ? `bonus:${bonus}` : penalty && penalty !== '0' ? `penalty:${penalty}` : 'none'
  return (
    <Select
      value={value}
      onValueChange={(nextValue) => {
        const [kind, count = ''] = nextValue.split(':')
        onChange(kind === 'bonus' ? count : '', kind === 'penalty' ? count : '')
      }}
    >
      <SelectTrigger className="w-auto min-w-28 text-xs" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="none">无奖惩骰</SelectItem>
        <SelectItem value="bonus:1">奖励骰 +1</SelectItem>
        <SelectItem value="bonus:2">奖励骰 +2</SelectItem>
        <SelectItem value="penalty:1">惩罚骰 +1</SelectItem>
        <SelectItem value="penalty:2">惩罚骰 +2</SelectItem>
      </SelectContent>
    </Select>
  )
}

function BlindToggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="inline-flex shrink-0 items-center gap-1.5 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      暗投
    </label>
  )
}

interface Props {
  sessionId: string
  action: KpAction
  fields: Record<string, string>
  workspace: Workspace | null
  setField: (key: string, value: string) => void
  setDiceAdjustment: (prefix: string, bonus: string, penalty: string) => void
  submit: () => Promise<void>
  slash: { palette: ReactNode; onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => boolean }
  dictating: boolean
  toggleDictation: () => void
}

export function KpActionForm({
  sessionId, action, fields, workspace, setField, setDiceAdjustment, submit, slash, dictating, toggleDictation,
}: Props) {
  const catalogs = catalogOptions(workspace)
  const defaultActorRef = workspace?.catalogs.characters?.[0]
    ? `character:${workspace.catalogs.characters[0].id}`
    : ''
  return (
    <>
      {action === 'narration' && (
        <div className="kp-narration-wrap flex w-full items-start gap-2">
          {slash.palette}
          <textarea
            value={fields.content || ''}
            onChange={(event) => setField('content', event.target.value)}
            onKeyDown={(event) => {
              if (slash.onKeyDown(event)) return
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) {
                event.preventDefault()
                void submit()
              }
            }}
            placeholder="输入要发布给全桌的旁白；打 / 唤起命令；（圆括号）内为场外；Ctrl/Cmd+Enter 发布"
            className="input min-h-16 min-w-0 flex-1 resize-y text-xs"
          />
          <button type="button" onClick={toggleDictation} className="btn-secondary !p-2" title="语音转文字">
            {dictating ? <MicOff size={15} /> : <Mic size={15} />}
          </button>
        </div>
      )}
      {action === 'dialogue' && <>
        <KpInput name="npc_id" placeholder="NPC 名称或 ID" options={catalogs.npcs} fields={fields} onChange={setField} />
        <textarea
          value={fields.content || ''}
          onChange={(event) => setField('content', event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
              event.preventDefault()
              void submit()
            }
          }}
          placeholder="台词内容（可连续输入；按 Ctrl/Cmd+Enter 发布）"
          className="input min-h-16 min-w-48 flex-1 resize-y text-xs"
        />
      </>}
      {action === 'dice_check' && <>
        <KpInput name="skill" placeholder="技能，如 幸运、侦查" fields={fields} onChange={setField} />
        <ActorSelect
          label="检定对象"
          value={fields.char || defaultActorRef}
          workspace={workspace}
          includeGroup
          onChange={(value) => setField('char', value)}
        />
        <Select value={fields.difficulty || 'normal'} onValueChange={(value) => setField('difficulty', value)}>
          <SelectTrigger className="w-auto min-w-20 text-xs" aria-label="检定难度">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="normal">普通</SelectItem>
            <SelectItem value="hard">困难</SelectItem>
            <SelectItem value="extreme">极难</SelectItem>
          </SelectContent>
        </Select>
        <DiceAdjustment
          label="检定奖惩骰"
          bonus={fields.bonus || ''}
          penalty={fields.penalty || ''}
          onChange={(bonus, penalty) => setDiceAdjustment('', bonus, penalty)}
        />
        <BlindToggle checked={fields.visibility === 'blind'} onChange={(checked) => setField('visibility', checked ? 'blind' : '')} />
        <KpInput name="source" placeholder="目标/来源（可选）" fields={fields} onChange={setField} />
        {/* 玩家在投骰按钮旁看到的「因：…」——只写他此刻已经知道的事，别用它剧透 */}
        <KpInput name="reason" placeholder="因何而检（玩家可见）" fields={fields} onChange={setField} />
        {/* 有奖惩骰才要理由：它会随骰子明细摆给玩家看，免得玩家以为被针对 */}
        {!!(fields.bonus || fields.penalty) && (
          <KpInput name="modifier_reason" placeholder="奖惩理由（玩家可见），如 烛光昏暗" fields={fields} onChange={setField} />
        )}
      </>}
      {action === 'opposed_check' && <>
        <div className="flex w-full flex-wrap items-center gap-2">
          <span className="w-10 shrink-0 text-xs font-semibold" style={{ color: 'var(--color-text-secondary)' }}>甲方</span>
          <ActorSelect
            label="甲方对象"
            value={fields.a || defaultActorRef}
            workspace={workspace}
            disabledValue={fields.b}
            onChange={(value) => setField('a', value)}
          />
          <KpInput name="a_skill" placeholder="甲方技能" fields={fields} onChange={setField} />
          <DiceAdjustment
            label="甲方奖惩骰"
            bonus={fields.a_bonus || ''}
            penalty={fields.a_penalty || ''}
            onChange={(bonus, penalty) => setDiceAdjustment('a_', bonus, penalty)}
          />
        </div>
        <div className="flex w-full flex-wrap items-center gap-2">
          <span className="w-10 shrink-0 text-xs font-semibold" style={{ color: 'var(--color-text-secondary)' }}>乙方</span>
          <ActorSelect
            label="乙方对象"
            value={fields.b || ''}
            workspace={workspace}
            disabledValue={fields.a || defaultActorRef}
            onChange={(value) => setField('b', value)}
          />
          <KpInput name="b_skill" placeholder="乙方技能" fields={fields} onChange={setField} />
          <DiceAdjustment
            label="乙方奖惩骰"
            bonus={fields.b_bonus || ''}
            penalty={fields.b_penalty || ''}
            onChange={(bonus, penalty) => setDiceAdjustment('b_', bonus, penalty)}
          />
        </div>
        <BlindToggle checked={fields.visibility === 'blind'} onChange={(checked) => setField('visibility', checked ? 'blind' : '')} />
      </>}
      {action === 'generic_roll' && <>
        <input type="number" min="1" max="20" value={fields.count || '1'} onChange={(event) => setField('count', event.target.value)} className="input w-20 text-xs" aria-label="骰子数量" title="骰子数量" />
        <span className="self-center text-xs" style={{ color: 'var(--color-text-secondary)' }}>颗</span>
        <input type="number" min="2" max="1000" value={fields.sides || '6'} onChange={(event) => setField('sides', event.target.value)} className="input w-24 text-xs" aria-label="骰子面数" title="骰子面数" />
        <span className="self-center text-xs" style={{ color: 'var(--color-text-secondary)' }}>面</span>
        <label className="inline-flex items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          <span className="shrink-0">修正</span>
          <input type="number" min="-10000" max="10000" value={fields.modifier || '0'} onChange={(event) => setField('modifier', event.target.value)} className="input w-24 text-xs" aria-label="结果修正值" />
        </label>
        <KpInput name="reason" placeholder="掷骰用途，如 参战敌人数" fields={fields} onChange={setField} />
        <BlindToggle checked={fields.visibility === 'blind'} onChange={(checked) => setField('visibility', checked ? 'blind' : '')} />
      </>}
      {action === 'san_check' && <>
        <KpInput name="chars" placeholder="目睹者，空=在场全体" fields={fields} onChange={setField} /><KpInput name="source" placeholder="恐怖源" fields={fields} onChange={setField} />
        <KpInput name="success_loss" placeholder="成功损失，如 0" fields={fields} onChange={setField} /><KpInput name="failure_loss" placeholder="失败损失，如 1d6" fields={fields} onChange={setField} />
        <KpInput name="reason" placeholder="因何而检（玩家可见，空=用恐怖源）" fields={fields} onChange={setField} />
      </>}
      {action === 'scene_change' && <NamedSelect label="场景" value={fields.scene_id || ''} items={workspace?.catalogs.scenes || []} onChange={(v) => setField('scene_id', v)} empty="本模组没有可切换的场景" />}
      {(action === 'set_flag' || action === 'clear_flag') && <KpInput name="flag" placeholder="剧情标志" fields={fields} onChange={setField} />}
      {action === 'set_mood' && <NamedSelect label="配乐氛围" value={fields.mood || ''} items={MOOD_ITEMS} onChange={(v) => setField('mood', v)} empty="" />}
      {action === 'handout' && <><GiScrollUnfurled size={16} /><NamedSelect label="手书" value={fields.id || ''} items={workspace?.catalogs.handouts || []} onChange={(v) => setField('id', v)} empty="本模组没有手书" /></>}
      {action === 'hp_change' && <HpChangeForm sessionId={sessionId} fields={fields} characters={workspace?.catalogs.characters || []} setField={setField} />}
      {action === 'mark_seen' && <MarkSeenForm fields={fields} clues={workspace?.catalogs.clues || []} setField={setField} />}
      {action === 'npc_act' && <NpcActForm fields={fields} npcs={workspace?.catalogs.npcs || []} setField={setField} />}
      {action === 'block_path' && <PlaceReasonForm fields={fields} scenes={workspace?.catalogs.scenes || []} setField={setField} reasonHint="为什么过不去（KP 备忘，可选）" />}
      {action === 'unblock_path' && <PlaceReasonForm fields={fields} scenes={workspace?.catalogs.scenes || []} setField={setField} />}
      {action === 'travel_suggest' && <PlaceReasonForm fields={fields} scenes={workspace?.catalogs.scenes || []} setField={setField} reasonHint="缘由（玩家可见，可选）" />}
      {action === 'start_combat' && <>
        <Swords size={16} />
        <MultiSelect
          value={(fields.enemies || '').split(',').map((item) => item.trim()).filter(Boolean)}
          options={(workspace?.catalogs.npcs || []).map((item) => ({ value: item.id, label: item.name }))}
          onValueChange={(value) => setField('enemies', value.join(','))}
          placeholder="选择参战敌人"
          aria-label="参战敌人"
          className="min-w-48 flex-1"
        />
        <KpInput name="trigger" placeholder="开战原因（可选）" fields={fields} onChange={setField} />
      </>}
      {/* 发起追逐：逃方（在座调查员）对追方（模组 NPC 或临场起名）。距离轨的三个数
          留空即取规则书建议值（起点 0、甩脱 5、被追上 −3），后端校验起点落在两端之间。 */}
      {action === 'start_chase' && <>
        <GiRun size={16} />
        <ActorSelect label="逃方" value={fields.quarry || ''} workspace={workspace} onChange={(v) => setField('quarry', v)} />
        <KpInput name="pursuer" placeholder="追方（NPC 名，或临场起名）" options={catalogs.npcs} fields={fields} onChange={setField} />
        <input value={fields.pursuer_mov || ''} onChange={(e) => setField('pursuer_mov', e.target.value)} placeholder="追方 MOV" className="input w-24 text-xs" inputMode="numeric" />
        <input value={fields.skill || ''} onChange={(e) => setField('skill', e.target.value)} placeholder="比拼技能（运动）" className="input w-32 text-xs" />
        <input value={fields.start_gap || ''} onChange={(e) => setField('start_gap', e.target.value)} placeholder="起始距离 0" className="input w-24 text-xs" inputMode="numeric" />
        <input value={fields.escape_at || ''} onChange={(e) => setField('escape_at', e.target.value)} placeholder="甩脱 5" className="input w-20 text-xs" inputMode="numeric" />
        <input value={fields.caught_at || ''} onChange={(e) => setField('caught_at', e.target.value)} placeholder="被追上 −3" className="input w-24 text-xs" inputMode="numeric" />
        <KpInput name="trigger" placeholder="起因（写进叙事，可选）" fields={fields} onChange={setField} />
      </>}
      {/* 结束战斗：主观收尾动作，没有参数——机械收场（击溃/全灭）走自动结算，
          这一条给 KP 主观裁定：敌人逃了、谈和了、或这一架不值得再打下去。 */}
      {action === 'end_combat' && (
        <div className="flex w-full items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          <GiSkullCrossedBones size={14} className="flex-shrink-0" style={{ color: 'var(--color-danger)' }} />
          <span>结束当前战斗：清出战场、落一条结果摘要，全桌回到叙事。战斗的收场（击溃 / 全灭）本会自动判定，这条用于 KP 主观叫停。</span>
        </div>
      )}
      {/* 解锁地点：此前只有「绕着写一句旁白去碰关键词匹配」这一条路，碰不碰得上全看措辞 */}
      {action === 'unlock_place' && <>
        <GiPadlock size={16} />
        <NamedSelect label="要对玩家公开的地点" value={fields.scene_id || ''} items={workspace?.catalogs.scenes || []} onChange={(v) => setField('scene_id', v)} empty="本模组没有地点" />
      </>}
      {/* 移动角色：玩家自己走要过封路与连通性的闸，KP 挪人是裁定结果，不走那条链 */}
      {action === 'move_actor' && <>
        <GiFootTrip size={16} />
        <KpInput name="character_id" placeholder="移动谁" options={catalogs.characters} fields={fields} onChange={setField} />
        <NamedSelect label="目的地" value={fields.scene_id || ''} items={workspace?.catalogs.scenes || []} onChange={(v) => setField('scene_id', v)} empty="本模组没有地点" />
      </>}
    </>
  )
}
