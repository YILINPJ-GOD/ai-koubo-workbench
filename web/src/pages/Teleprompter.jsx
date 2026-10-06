import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

/**
 * 全屏提词器：逐句大字推进 + 关键词提纲模式 + 自动播放。
 * 拍摄时把设备放在镜头旁，空格/点击推进到下一句。
 */
export default function Teleprompter({ slot, lines, onClose }) {
  // lines: [{text, keys?}]
  const [idx, setIdx] = useState(0)
  const [mode, setMode] = useState('full') // full | keys
  const [auto, setAuto] = useState(false)
  const [speed, setSpeed] = useState(5) // 语速：字/秒
  const timerRef = useRef(null)

  const next = useCallback(() => setIdx((i) => Math.min(i + 1, lines.length - 1)), [lines.length])
  const prev = useCallback(() => setIdx((i) => Math.max(i - 1, 0)), [])

  // 键盘：空格/→ 推进，← 回退，Esc 退出
  useEffect(() => {
    const onKey = (e) => {
      if (e.code === 'Space' || e.key === 'ArrowRight') {
        e.preventDefault()
        next()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        prev()
      } else if (e.key === 'Escape') {
        onClose()
      } else if (e.key.toLowerCase() === 'k') {
        setMode((m) => (m === 'full' ? 'keys' : 'full'))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, prev, onClose])

  // 自动播放：按当前句字数估算停留时长（语速 speed 字/秒，最少 1.2s）
  useEffect(() => {
    if (!auto) return undefined
    if (idx >= lines.length - 1) {
      setAuto(false)
      return undefined
    }
    const chars = (lines[idx]?.text || '').replace(/[，。！？、：；,.!?;:\s]/g, '').length
    const ms = Math.max(1200, (chars / Math.max(2, speed)) * 1000)
    timerRef.current = setTimeout(next, ms)
    return () => clearTimeout(timerRef.current)
  }, [auto, idx, lines, speed, next])

  const totalChars = useMemo(
    () => lines.reduce((s, l) => s + (l.text || '').length, 0),
    [lines],
  )
  const remainSec = useMemo(
    () =>
      Math.round(
        lines.slice(idx).reduce((s, l) => s + (l.text || '').length, 0) / Math.max(2, speed),
      ),
    [lines, idx, speed],
  )

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-gray-950 text-white"
      onClick={next}
    >
      {/* 顶部工具栏（点击不推进） */}
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-800 px-4 py-2 text-xs" onClick={(e) => e.stopPropagation()}>
        <span className="badge bg-amber-100 text-amber-800">{slot} 提词器</span>
        <button
          className={`chip ${mode === 'keys' ? 'chip-on' : 'chip-off'} !text-xs`}
          onClick={() => setMode(mode === 'full' ? 'keys' : 'full')}
          title="按 K 切换"
        >
          {mode === 'keys' ? '🔑 关键词提纲' : '📝 全文'}
        </button>
        <button className={`chip ${auto ? 'chip-on' : 'chip-off'} !text-xs`} onClick={() => setAuto(!auto)}>
          {auto ? '⏸ 停止自动' : '▶ 自动播放'}
        </button>
        {auto && (
          <label className="flex items-center gap-1 text-gray-400">
            语速
            <input
              type="range"
              min={3}
              max={8}
              value={speed}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setSpeed(Number(e.target.value))}
            />
            {speed}字/秒
          </label>
        )}
        <span className="text-gray-500">
          句 {idx + 1}/{lines.length} · 剩余约 {remainSec}s · 空格/点击推进，←回退，K切提纲，Esc退出
        </span>
        <button className="ml-auto btn-ghost !text-xs" onClick={onClose}>退出 (Esc)</button>
      </div>

      {/* 进度条 */}
      <div className="h-1 w-full bg-gray-800">
        <div
          className="h-1 bg-brand-500 transition-all duration-200"
          style={{ width: `${lines.length ? ((idx + 1) / lines.length) * 100 : 0}%` }}
        />
      </div>

      {/* 提词主体 */}
      <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-8 py-10 text-center">
        {lines.map((l, i) => {
          if (i < idx - 1) return null // 已念过的只留上一句做参照
          const isCur = i === idx
          const isNext = i === idx + 1
          if (mode === 'keys') {
            return (
              <div key={i} className={`my-3 ${isCur ? '' : isNext ? 'opacity-40' : 'hidden'}`}>
                {isCur && (
                  <>
                    <div className="text-5xl font-bold tracking-widest text-amber-300">
                      {(l.keys || []).join(' · ') || l.text.slice(0, 12)}
                    </div>
                    {l.keys?.length > 0 && (
                      <div className="mt-4 text-lg text-gray-500">{l.text}</div>
                    )}
                  </>
                )}
              </div>
            )
          }
          return (
            <div
              key={i}
              className={
                isCur
                  ? 'my-4 text-4xl font-bold leading-relaxed text-white sm:text-5xl'
                  : isNext
                    ? 'my-3 text-xl text-gray-500'
                    : 'hidden'
              }
            >
              {l.text}
            </div>
          )
        })}
        {idx >= lines.length - 1 && (
          <p className="mt-8 text-2xl font-bold text-emerald-400">✓ 念完了，开拍！</p>
        )}
      </div>
    </div>
  )
}
