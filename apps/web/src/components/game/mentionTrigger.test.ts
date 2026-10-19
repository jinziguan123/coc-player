import { describe, expect, it } from 'vitest'

import { caretOf, completeMention, detectMention, isBoundaryChar } from './mentionTrigger'

/**
 * 用例写法（两个标记各表示一件事，互不干扰）：
 *   `^` = @ 所在的位置，由 helper 插进去；
 *   `$` = 可选，标光标在哪（省略＝紧跟触发段之后，即「打完字就选」）。
 *
 * 为什么不让用例直接写 @：那样「被测的 @」和「光标在哪」混在同一串里，标记一写歪就变成
 * 在测另一段文本，而失败信息长得一模一样。
 */
/** 少了 ^ 就当场报错：helper 会在 indexOf 返回 -1 时把 @ 插到倒数第一个字符前，
 *  于是「我没写标记」会悄悄变成「在测另一句话」——失败信息还长得一模一样。 */
function build(marked: string): { text: string; caret: number } {
  const mark = marked.indexOf('^')
  if (mark < 0) throw new Error(`用例缺 ^ 标记：${marked}`)
  const caretMark = marked.indexOf('$')
  const bare = marked.replace('^', '').replace('$', '')
  return {
    text: bare.slice(0, mark) + '@' + bare.slice(mark),
    // 光标 = 它在\(新文本里的位置 + @ 自己占下的那一个字符
    caret: (caretMark < 0 ? bare.length : caretMark - 1) + 1,
  }
}

function detect(marked: string) {
  const { text, caret } = build(marked)
  return { text, caret, trigger: detectMention(text, caret) }
}

describe('isBoundaryChar', () => {
  it('行首、空白、标点算起头', () => {
    for (const ch of ['', ' ', '\n', '：', '，', '「', '（', '-', '—', '"']) {
      expect(isBoundaryChar(ch)).toBe(true)
    }
  })

  it('标识符字符不算起头：字母、数字、下划线（邮箱与代码里的 @ 长这样）', () => {
    for (const ch of ['a', 'Z', '0', '_']) {
      expect(isBoundaryChar(ch)).toBe(false)
    }
  })

  it('汉字/假名/谚文算起头：中文不加空格，「我对@艾琳说」是常态', () => {
    for (const ch of ['对', '我', 'あ', '한']) {
      expect(isBoundaryChar(ch)).toBe(true)
    }
  })
})

describe('detectMention', () => {
  it('行首打 @ 就唤起，query 为空', () => {
    expect(detect('^').trigger).toEqual({ at: 0, query: '', from: 0, to: 1 })
  })

  it('标点之后打 @ 就唤起——这正是与斜杠命令相反的地方（斜杠只认串首）', () => {
    expect(detect('他说：^管').trigger).toEqual({ at: 3, query: '管', from: 3, to: 5 })
  })

  it('汉字紧跟 @ 也能起头：中文句子不加空格（只有邮箱那种标识符前才不弹）', () => {
    expect(detect('我对^艾琳说').trigger).toEqual({ at: 2, query: '艾琳说', from: 2, to: 6 })
    expect(detect('我对 ^艾琳说').trigger).toEqual({ at: 3, query: '艾琳说', from: 3, to: 7 })
  })

  it('读光标而不是读串尾：光标在名字中间时，替换范围只到光标', () => {
    expect(detect('我对 ^艾$琳说').trigger).toEqual({ at: 3, query: '艾', from: 3, to: 5 })
  })

  it('光标已在 @ 之前（往回退）就不该再弹', () => {
    expect(detectMention('我对@艾琳说', 2)).toBeNull()
  })

  it('整串没有 @ 时返回 null', () => {
    // 这一例没有 @ 可插，直接调纯函数（helper 会补 @，语义对不上）
    expect(detectMention('我推开门', 4)).toBeNull()
  })

  it('邮箱里的 @ 不算触发：前一个字符是字母', () => {
    expect(detect('发到 lin^example').trigger).toBeNull()
  })

  it('@ 后面出现空格即失效：不再有「怎么关掉面板」这个问题', () => {
    expect(detect('^艾琳 你好').trigger).toBeNull()
  })

  it('第二个 @ 重新起头，取最近的那个', () => {
    expect(detect('艾琳 ^伦').trigger).toEqual({ at: 3, query: '伦', from: 3, to: 5 })
  })

  it('引号、括号后也可以起头', () => {
    expect(detect('他喊「^管').trigger).toEqual({ at: 3, query: '管', from: 3, to: 5 })
    expect(detect('（^伦').trigger).toEqual({ at: 1, query: '伦', from: 1, to: 3 })
  })

  it('换行后可以起头', () => {
    expect(detect('第一行\n^管').trigger).toEqual({ at: 4, query: '管', from: 4, to: 6 })
  })

  it('夹住越界光标，不抛异常', () => {
    expect(detectMention('@艾', 99)).toEqual({ at: 0, query: '艾', from: 0, to: 2 })
    expect(detectMention('@艾', -5)).toBeNull()
  })
})

describe('completeMention', () => {
  const trigger = { from: 2, to: 4 }

  it('中文名后不补空格，光标停在名字右侧', () => {
    // from..to 覆盖 @艾琳：替换后原样，只是名字按选中项重写了一遍
    expect(completeMention('我对@艾琳说', trigger, '艾琳')).toEqual({
      text: '我对@艾琳琳说', caret: 5,
    })
  })

  it('拉丁名后补一个空格，免得跟正文粘成一个词', () => {
    expect(completeMention('找@Sar', { from: 1, to: 5 }, 'Sarah')).toEqual({
      text: '找@Sarah ', caret: 8,
    })
  })

  it('后面本来就是空格就不补第二个', () => {
    expect(completeMention('找@Sar 一起', { from: 1, to: 5 }, 'Sarah')).toEqual({
      text: '找@Sarah 一起', caret: 7,
    })
  })

  it('可以显式不要空格（接着要写标点）', () => {
    expect(completeMention('找@Sar', { from: 1, to: 5 }, 'Sarah', false)).toEqual({
      text: '找@Sarah', caret: 7,
    })
  })

  it('只替换 @ 到光标这一段，光标后面的原文原样保留', () => {
    expect(completeMention('我对@艾说下去', { from: 2, to: 3 }, '艾琳', false)).toEqual({
      text: '我对@艾琳艾说下去', caret: 5,
    })
  })

  it('光标停在名字中间时，替换范围只吃 @ 到光标这一段', () => {
    const { text, caret } = build('我对 ^艾$琳说')
    const hit = detectMention(text, caret)!
    expect(completeMention(text, hit, '艾琳', false)).toEqual({
      text: '我对 @艾琳琳说', caret: 6,
    })
  })

  it('名字两边的空白先去掉，不会插出「@ 名字」', () => {
    expect(completeMention('@x', { from: 0, to: 2 }, ' 艾琳 ')).toEqual({
      text: '@艾琳', caret: 3,
    })
  })
})

describe('caretOf', () => {
  it('取原生光标位置', () => {
    expect(caretOf({ selectionStart: 3, value: 'abcdef' })).toBe(3)
  })

  it('原生控件不认时退回串尾', () => {
    expect(caretOf({ selectionStart: null, value: 'abc' })).toBe(3)
  })
})
