import { m } from 'motion/react'
import { DecoSunburst } from '@/components/ui/deco'
import { absoluteImageUrl } from '@/lib/imageUrl'
import { ease } from '@/lib/motion'

/**
 * 模组详情顶部的封面横幅：像把那本书翻开到扉页。
 * 配图取开场场景（与模组卡同一取法）；没有时是日芒纹。标题逐字浮起、封面图缓缓推近。
 */
export function ModuleHero({ title, cover, facts }: { title: string; cover?: string; facts: string[] }) {
  return (
    <div className={`module-hero ${cover ? '' : 'module-hero--bare'}`}>
      {cover ? (
        <m.img
          src={absoluteImageUrl(cover)}
          alt=""
          className="module-hero-img"
          initial={{ scale: 1.15, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 1.6, ease: ease.out }}
        />
      ) : (
        <DecoSunburst className="module-hero-sunburst" />
      )}
      <div className="module-hero-scrim" aria-hidden />
      <div className="module-hero-body">
        <h1 className="module-hero-title" aria-label={title}>
          {[...title].map((ch, i) => (
            <m.span
              key={`${ch}-${i}`}
              aria-hidden
              initial={{ opacity: 0, y: 18, filter: 'blur(6px)' }}
              animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
              transition={{ duration: 0.6, delay: 0.15 + i * 0.05, ease: ease.out }}
            >
              {ch}
            </m.span>
          ))}
        </h1>
        {facts.length > 0 && (
          <m.p
            className="module-hero-facts"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.35 + title.length * 0.05 }}
          >
            {facts.join(' · ')}
          </m.p>
        )}
      </div>
    </div>
  )
}
