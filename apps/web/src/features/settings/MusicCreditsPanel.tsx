/**
 * 设置 → 外观 → 背景音乐：说明配乐怎么工作、自定义曲库放哪，并列出内置曲目的署名。
 *
 * 署名不是客套：内置曲目多为 CC-BY，许可证要求在使用处给出可见的作者与许可信息。
 * 数据来自后端 server/assets/bgm/credits.json（唯一真源），这里只负责展示。
 */
import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { MOOD_LABELS } from '@/features/game-session/bgm'

interface CreditItem {
  mood: string
  file: string
  title: string
  author: string
  license: string
  license_url: string
  source: string
}

const linkStyle = { color: 'var(--color-text-accent)' }

export function MusicCreditsPanel() {
  const [credits, setCredits] = useState<CreditItem[] | null>(null)
  useEffect(() => {
    let stale = false
    api.get<{ tracks: CreditItem[] }>('/bgm/credits')
      .then((r) => { if (!stale) setCredits(r.tracks || []) })
      .catch(() => { if (!stale) setCredits([]) })
    return () => { stale = true }
  }, [])

  return (
    <div className="card">
      <h3 className="card-title">背景音乐</h3>
      <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginBottom: '0.85rem', lineHeight: 1.7 }}>
        对局中按氛围自动配乐：战斗、追逐自动换曲，平时跟随所在场景（模组导入时标注），
        KP 也可临时改换。开关与音量在对局顶栏，每台设备各自保存。
        <br />
        想换成自己的曲子：在数据目录下建 <code>bgm/&lt;氛围&gt;/</code> 放入 mp3 / ogg
        （源码运行时是 <code>server/bgm/</code>），氛围目录名为
        calm / explore / tense / horror / mythos / sorrow / combat / chase；
        某个氛围放了自己的曲子，就整组替换内置曲目。
      </p>
      <details>
        <summary className="text-sm" style={{ cursor: 'pointer' }}>
          音乐致谢（内置 {credits?.length ?? '…'} 首）
        </summary>
        {credits && credits.length === 0 && (
          <p className="text-xs" style={{ color: 'var(--color-text-secondary)', marginTop: '0.5rem' }}>
            暂无内置曲目信息。
          </p>
        )}
        <ul className="text-xs" style={{ marginTop: '0.6rem', display: 'grid', gap: '0.35rem' }}>
          {(credits || []).map((c) => (
            <li key={`${c.mood}/${c.file}`} style={{ color: 'var(--color-text-secondary)' }}>
              <span style={{ color: 'var(--color-text-primary)' }}>「{c.title}」</span>
              {' '}{c.author}
              {' · '}{MOOD_LABELS[c.mood] || c.mood}
              {' · '}<a href={c.license_url} target="_blank" rel="noreferrer" style={linkStyle}>{c.license}</a>
              {' · '}<a href={c.source} target="_blank" rel="noreferrer" style={linkStyle}>来源</a>
            </li>
          ))}
        </ul>
      </details>
    </div>
  )
}
