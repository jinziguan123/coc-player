/**
 * 输入框里的 @ 触发与补全插入：纯函数，不碰 DOM。
 *
 * 与斜杠命令（useSlashCommands）不是一回事，别照着抄：斜杠只在**整串以 / 开头**时唤起，
 * 因为写旁白时句中冒出的斜杠（网址、日期）不该弹面板；@ 恰恰相反——它天生出现在句中
 * （「我对@艾琳说」），判定必须落在**光标处**，替换的也只是 @ 到光标这一段。
 *
 * 这层单独成文件是为了能直接测：这是本功能唯一真正会出 bug 的地方（边界、替换范围、
 * 插入后的光标位）。
 */

/** 触发段：文本里从 `@` 到光标的这一段，以及它在原文中的位置。 */
export interface MentionTrigger {
  /** 触发符号在整个文本中的下标（`text[at]` 必为 '@'）。 */
  at: number
  /** at 到光标之间的文字（不含 @），过滤候选用的词。 */
  query: string
  /** 替换范围起点（含），恒等于 at。 */
  from: number
  /** 替换范围终点（不含），恒等于光标位置。 */
  to: number
}

/** 前一个字符是不是「标识符里那种字符」：ASCII 字母、数字、下划线。
 *
 *  只有这些才算「词内」——邮箱（lin@example）、代码里的 @ 就在这种位置，那里弹面板纯打扰。
 *
 *  **汉字/假名/谚文刻意不算词内**：中文写作不加空格，「我对@艾琳说」是常态；按「前一个字符
 *  是汉字就不触发」会把最常见的用法堵死（曾经就这么写，接上正文 token 渲染时一测就露了）。
 *  逐字符判断而不是写字符类正则：字符类里塞括号字符极易被读成嵌套区间，且很难发现。 */
const IDENT_CHAR = /[0-9A-Za-z_]/

/** 光标前的字符算不算「起头」：行首、空白、标点、汉字都算，只有标识符字符不算。 */
export function isBoundaryChar(prev: string): boolean {
  return !IDENT_CHAR.test(prev)
}

/**
 * 光标处是否正处在一个 @ 触发段里。返回 null 表示不该弹面板。
 *
 * @param caret 光标位置，超出范围会被夹到合法区间（受控组件在 onChange 之间取不到位置时用得上）。
 */
export function detectMention(text: string, caret: number): MentionTrigger | null {
  const to = Math.max(0, Math.min(caret, text.length))
  const head = text.slice(0, to)
  const at = head.lastIndexOf('@')
  if (at < 0) return null
  const query = head.slice(at + 1)
  // 不能有空白、也不能再有一个 @：于是「@艾琳 你好」在打完空格后自动失效，
  // 不必额外写关闭逻辑。
  if (query.includes('@') || /\s/.test(query)) return null
  if (!isBoundaryChar(text.slice(0, at).slice(-1))) return null
  return { at, query, from: at, to }
}

/** CJK、全角标点、假名、谚文：这些字后面不该补空格，补了反而跟正文隔一层。 */
const CJK = /[\u3000-\u9fff\uff00-\uffef\u3040-\u30ff\uac00-\ud7af]/

export interface MentionCompletion {
  text: string
  /** 插入后的光标位置（紧跟名字），调用方据此把光标放回来。 */
  caret: number
}

/**
 * 把触发段换成选中的候选名。
 *
 * @param withSpace 选中项后面补不补空格。默认：名字以非 CJK 收尾就补，免得「@Sarah走了」
 *                  粘成一个词。调用方显式传 false 表示「我还要接标点」。
 */
export function completeMention(
  text: string,
  trigger: Pick<MentionTrigger, 'from' | 'to'>,
  label: string,
  withSpace?: boolean,
): MentionCompletion {
  const name = label.trim()
  const rest = text.slice(trigger.to)
  const needSpace = withSpace ?? !(CJK.test(name.slice(-1)) || rest.startsWith(' '))
  const inserted = `@${name}${needSpace ? ' ' : ''}`
  return {
    text: text.slice(0, trigger.from) + inserted + rest,
    caret: trigger.from + inserted.length,
  }
}

/** 输入框自己报的光标位置；原生控件不认时退回串尾（玩家多半在末尾接着写）。 */
export function caretOf(el: { selectionStart: number | null; value: string }): number {
  return el.selectionStart ?? el.value.length
}
