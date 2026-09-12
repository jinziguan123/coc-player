/**
 * 语音转文字：KP 口述旁白时不必打字。
 *
 * 从 HumanKpPanel 抽出来——它与 KP 工作台的其余部分没有任何耦合（既不碰动作、也不碰
 * 工作区），却带着一整套 Web Speech API 的类型声明与生命周期，留在那个文件里只是占地方。
 *
 * 浏览器支持度不齐（Safari 走 webkit 前缀，Firefox 干脆没有），所以不支持时只提示一句，
 * 不把按钮藏起来——藏起来会让人以为是功能坏了。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

interface SpeechRecognitionResultEventLike {
  results: ArrayLike<{ 0: { transcript: string } }>
}

interface SpeechRecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: SpeechRecognitionResultEventLike) => void) | null
  onerror: (() => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike

/** onText 收到的是**增量**转写结果，调用方自行决定追加到哪个字段。 */
export function useDictation(onText: (chunk: string) => void) {
  const [dictating, setDictating] = useState(false)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  // 回调存 ref：识别实例只建一次，但调用方的闭包每次渲染都换，直接捕获会写到旧的字段上。
  const sink = useRef(onText)
  sink.current = onText

  // 组件卸载时务必停掉：麦克风指示灯不会自己熄。
  useEffect(() => () => recognitionRef.current?.stop(), [])

  const toggle = useCallback(() => {
    if (recognitionRef.current && dictating) {
      recognitionRef.current.stop()
      return
    }
    const speechWindow = window as Window & {
      SpeechRecognition?: SpeechRecognitionCtor
      webkitSpeechRecognition?: SpeechRecognitionCtor
    }
    const Recognition = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
    if (!Recognition) {
      toast.error('当前浏览器不支持语音转文字')
      return
    }
    const recognition = new Recognition()
    recognition.lang = 'zh-CN'
    recognition.interimResults = false
    recognition.continuous = true
    recognition.onresult = (event) => {
      sink.current(Array.from(event.results).map((r) => r[0].transcript).join(''))
    }
    recognition.onerror = () => { setDictating(false); toast.error('语音识别中断') }
    recognition.onend = () => setDictating(false)
    recognitionRef.current = recognition
    setDictating(true)
    recognition.start()
  }, [dictating])

  return { dictating, toggle }
}
