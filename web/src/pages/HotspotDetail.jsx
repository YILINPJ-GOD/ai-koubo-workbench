import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, runJob, usePollWhile } from '../api'
import { useToast } from '../toast'
import { StatusBadge } from './Today'

const SLOTS = ['15s', '30s', '60s']

function copyText(toast, text, label = '已复制') {
  navigator.clipboard
    .writeText(text)
    .then(() => toast(label, 'success'))
    .catch(() => toast('复制失败，请手动选择文本', 'error'))
}

function highlightRisks(text, risks) {
  const words = (risks || []).map((r) => r.word)
  if (!words.length || !text) return text
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const re = new RegExp(`(${escaped.join('|')})`, 'g')
  return text.split(re).map((part, i) =>
    words.includes(part) ? (
      <mark key={i} className="rounded bg-amber-200 px-0.5 text-amber-900" title="风险词，建议替换">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

function SourceList({ sources }) {
  return (
    <div className="space-y-2">
      {sources.map((s) => (
        <a
          key={s.id}
          href={s.url}
          target="_blank"
          rel="noreferrer"
          className="block rounded-lg border border-gray-100 px-3 py-2 text-sm hover:bg-gray-50"
        >
          <div className="flex items-center gap-2">
            <span
              className={`badge ${
                s.source_type === 'official'
                  ? 'bg-blue-100 text-blue-700'
                  : s.source_type === 'overseas'
                    ? 'bg-purple-100 text-purple-700'
                    : 'bg-emerald-100 text-emerald-700'
              }`}
            >
              {{ official: '官方', media: '媒体', overseas: '海外', trending: '热榜' }[s.source_type]}
            </span>
            <span className="truncate">{s.title}</span>
            <span className="ml-auto shrink-0 text-xs text-brand-600">↗</span>
          </div>
          {s.summary && <p className="mt-1 truncate text-xs text-gray-500">{s.summary}</p>}
        </a>
      ))}
    </div>
  )
}

function ImagePanel({ pack, packId, toast, qc }) {
  const candidates = pack.image_candidates || []
  const selected = pack.image_selected || []
  const [preview, setPreview] = useState(null)

  const selectMutation = useMutation({
    mutationFn: (sel) => api.put(`/api/packs/${packId}/images`, { selected: sel }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hotspot'] }),
  })
  const fileUrl = (idx) => `/api/packs/${packId}/images/${idx}/file`

  return (
    <div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {candidates.map((c, idx) => {
          const on = selected.includes(idx)
          return (
            <div
              key={idx}
              className={`relative cursor-pointer overflow-hidden rounded-lg border-2 transition ${
                on ? 'border-brand-600' : 'border-transparent hover:border-gray-300'
              }`}
              onClick={() =>
                selectMutation.mutate(
                  on ? selected.filter((i) => i !== idx) : [...selected, idx],
                )
              }
            >
              <img src={fileUrl(idx)} className="h-32 w-full object-cover" />
              <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white">
                {on ? '✓ 已选用' : c.type === 'card' ? '文字卡片' : '原文配图'}
              </span>
              <button
                className="absolute right-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-xs text-white"
                onClick={(e) => {
                  e.stopPropagation()
                  setPreview(idx)
                }}
              >
                放大
              </button>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          className="btn-ghost text-xs"
          onClick={async () => {
            try {
              const res = await fetch(`/api/packs/${packId}/images/download`)
              if (!res.ok) {
                const body = await res.json().catch(() => ({}))
                throw new Error(body.detail || '下载失败')
              }
              const blob = await res.blob()
              const a = document.createElement('a')
              a.href = URL.createObjectURL(blob)
              a.download = `pack_${packId}_images.zip`
              a.click()
              URL.revokeObjectURL(a.href)
              toast('已下载选中配图', 'success')
            } catch (e) {
              toast(e.message || '下载失败', 'error')
            }
          }}
        >
          ⬇ 下载选中配图（{selected.length}张）
        </button>
        {candidates.some((c) => c.type === 'remote') && (
          <span className="text-xs text-gray-400">原文配图版权归原站所有，商用注意</span>
        )}
      </div>
      {preview !== null && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8"
          onClick={() => setPreview(null)}
        >
          <img src={fileUrl(preview)} className="max-h-full max-w-full rounded-lg" />
        </div>
      )}
    </div>
  )
}

export default function HotspotDetail() {
  const { id } = useParams()
  const [searchParams] = useSearchParams()
  const toast = useToast()
  const qc = useQueryClient()

  const { data, isLoading, error } = useQuery({
    queryKey: ['hotspot', id],
    queryFn: () => api.get(`/api/hotspots/${id}`),
  })
  const stylesQuery = useQuery({
    queryKey: ['styles'],
    queryFn: () => api.get('/api/styles'),
    retry: false,
  })
  const styles = stylesQuery.data?.styles || []
  const [styleId, setStyleId] = useState(searchParams.get('style') || '')

  const [slot, setSlot] = useState(null)
  const activeSlot = slot || data?.hotspot?.suggested_length || '30s'
  const [generating, setGenerating] = useState(false)
  const [genProgress, setGenProgress] = useState('')
  usePollWhile(generating, 2500)

  const genMutation = useMutation({
    mutationFn: () => api.post(`/api/hotspots/${id}/pack`, { style_id: styleId ? Number(styleId) : null }),
  })
  const shotMutation = useMutation({
    mutationFn: () => api.post(`/api/hotspots/${id}/mark-shot`),
    onSuccess: () => {
      toast('已标记为已拍 ✅', 'success')
      qc.invalidateQueries()
    },
  })

  const generate = () => {
    if (generating) return
    setGenerating(true)
    setGenProgress('准备生成…')
    runJob(genMutation, {
      onJob: (job) =>
        setGenProgress(job.message || job.stage || '生成中，约需半分钟…'),
      pollMs: 2000,
    })
      .then(async (result) => {
        await qc.invalidateQueries()
        if (result?.warnings?.length) {
          toast(result.warnings[0], 'info')
        } else {
          toast('素材包已生成', 'success')
        }
      })
      .catch((e) => toast(e.message || '生成失败', 'error'))
      .finally(() => {
        setGenerating(false)
        setGenProgress('')
      })
  }

  const packText = useMemo(() => {
    if (!data?.pack) return ''
    const p = data.pack
    const lines = [
      `【选题】${data.hotspot.title}`,
      `【口播稿 · ${activeSlot}】`,
      p.scripts[activeSlot] || '',
      `【分镜字幕 · ${activeSlot}】`,
      ...(p.captions[activeSlot] || []).map((c) => `${c.t} ${c.text}`),
      '【发布文案】',
      `标题备选：${(p.publish.titles || []).map((t, i) => `${i + 1}.${t}`).join('  ')}`,
      `话题标签：${(p.publish.tags || []).join(' ')}`,
      `封面大字：${p.publish.cover_text || ''}`,
    ]
    return lines.filter((l) => l !== '').join('\n')
  }, [data, activeSlot])

  if (isLoading) return <div className="py-20 text-center text-gray-400">加载中…</div>
  if (error || !data?.hotspot)
    return (
      <div className="py-20 text-center text-gray-400">
        热点不存在。<Link className="text-brand-600" to="/hotspots">返回热点列表</Link>
      </div>
    )

  const { hotspot: h, sources, pack } = data
  const script = pack?.scripts?.[activeSlot] || ''
  const captions = pack?.captions?.[activeSlot] || []
  const slotChecks = { '15s': '80-110字', '30s': '130-200字', '60s': '270-360字' }

  return (
    <div className="space-y-4">
      <Link to="/hotspots" className="text-sm text-gray-500 hover:text-gray-700">← 返回热点列表</Link>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        {/* 左：事件区 */}
        <div className="space-y-4 lg:col-span-2">
          <div className="card p-5">
            <div className="flex items-start justify-between gap-2">
              <h1 className="text-lg font-bold leading-7">{h.title}</h1>
              <span className="badge shrink-0 bg-amber-100 text-amber-700">{h.suggested_length}</span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <StatusBadge status={h.status} />
              {h.sources_count >= 2 && (
                <span className="badge bg-emerald-50 text-emerald-700">✓{h.sources_count}个独立来源确认</span>
              )}
              {h.sequel_of && <span className="badge bg-purple-50 text-purple-700">📌 续集</span>}
            </div>
            {h.why && <p className="mt-3 text-sm text-gray-600">{h.why}</p>}
            {h.angles?.length > 0 && (
              <p className="mt-2 text-xs text-gray-500">切入角度：{h.angles.join(' / ')}</p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <button className="btn-primary" onClick={generate} disabled={generating}>
                {generating ? '生成中…' : pack ? '重新生成素材包' : '生成素材包'}
              </button>
              {h.status !== 'shot' && (
                <button className="btn-ghost" onClick={() => shotMutation.mutate()} disabled={shotMutation.isPending}>
                  标为已拍 ✅
                </button>
              )}
            </div>
            {generating && (
              <p className="mt-2 animate-pulse text-sm text-brand-600">{genProgress}</p>
            )}
            {pack?.style_note && <p className="mt-2 text-xs text-purple-600">{pack.style_note}</p>}
            {pack && pack.wordcount_ok === 0 && (
              <p className="mt-2 text-xs text-amber-600">⚠ 有档位字数未达标，建议重新生成</p>
            )}
          </div>

          <div className="card p-5">
            <h2 className="mb-3 text-sm font-semibold">来源（{sources.length}）{h.sources_count >= 2 ? '· 已交叉确认' : ''}</h2>
            <SourceList sources={sources} />
          </div>

          {/* 风险词与自查 */}
          {pack && (pack.risks?.length > 0 || pack.checklist?.length > 0) && (
            <div className="card p-5">
              <h2 className="mb-2 text-sm font-semibold">安全检查</h2>
              {pack.risks?.length > 0 && (
                <div className="mb-3 space-y-1">
                  {pack.risks.map((r, i) => (
                    <p key={i} className="text-xs text-amber-700">
                      ⚠「{r.word}」— {r.suggestion}
                    </p>
                  ))}
                </div>
              )}
              <ul className="space-y-1 text-xs text-gray-500">
                {pack.checklist.map((c, i) => (
                  <li key={i}>□ {c}</li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* 右：素材区 */}
        <div className="space-y-4 lg:col-span-3">
          {!pack && !generating && (
            <div className="card flex min-h-64 flex-col items-center justify-center p-10 text-center">
              <div className="text-3xl">📦</div>
              <p className="mt-2 text-sm text-gray-500">
                还没有素材包。点左侧「生成素材包」，一次性产出三档口播稿、字幕、配图和发布文案，约半分钟。
              </p>
            </div>
          )}
          {generating && !pack && (
            <div className="card flex min-h-64 flex-col items-center justify-center p-10">
              <div className="h-8 w-8 animate-spin rounded-full border-4 border-brand-100 border-t-brand-600" />
              <p className="mt-3 text-sm text-gray-500">{genProgress}</p>
            </div>
          )}

          {pack && (
            <>
              {/* 档位切换 */}
              <div className="card flex flex-wrap items-center justify-between gap-2 p-3">
                <div className="flex gap-1">
                  {SLOTS.map((s) => (
                    <button
                      key={s}
                      className={`chip ${activeSlot === s ? 'chip-on' : 'chip-off'}`}
                      onClick={() => setSlot(s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400">{slotChecks[activeSlot]}</span>
                  <select
                    className="rounded-lg border border-gray-200 px-2 py-1 text-xs"
                    value={styleId}
                    onChange={(e) => setStyleId(e.target.value)}
                  >
                    <option value="">默认人设</option>
                    {styles.map((s) => (
                      <option key={s.id} value={s.id}>模板：{s.name}</option>
                    ))}
                  </select>
                  <button
                    className="btn-ghost text-xs"
                    onClick={() => copyText(toast, packText, '整包已复制')}
                  >
                    复制整包
                  </button>
                </div>
              </div>

              {/* 口播稿 + 分镜字幕 */}
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div className="card p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">口播稿（{activeSlot}）</h3>
                    <button
                      className="text-xs text-brand-600 hover:underline"
                      onClick={() => copyText(toast, script, '口播稿已复制')}
                    >
                      复制
                    </button>
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-7">{highlightRisks(script, pack.risks)}</p>
                </div>
                <div className="card p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">分镜字幕（{activeSlot}）</h3>
                    <button
                      className="text-xs text-brand-600 hover:underline"
                      onClick={() =>
                        copyText(
                          toast,
                          captions.map((c) => `${c.t} ${c.text}`).join('\n'),
                          '字幕已复制',
                        )
                      }
                    >
                      复制
                    </button>
                  </div>
                  <div className="space-y-2">
                    {captions.length === 0 && <p className="text-sm text-gray-400">（无）</p>}
                    {captions.map((c, i) => (
                      <div key={i} className="flex gap-2 text-sm">
                        <span className="shrink-0 badge bg-gray-100 text-gray-500">{c.t}</span>
                        <span className={c.gold ? 'font-bold text-amber-600' : ''}>{c.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* 配图 */}
              <div className="card p-4">
                <h3 className="mb-3 text-sm font-semibold">配图（勾选 1~2 张，点图预览）</h3>
                <ImagePanel pack={pack} packId={pack.id} toast={toast} qc={qc} />
              </div>

              {/* 发布文案 */}
              <div className="card p-4">
                <h3 className="mb-3 text-sm font-semibold">发布文案</h3>
                <div className="space-y-3 text-sm">
                  <div>
                    <p className="mb-1 text-xs text-gray-400">标题备选（点击复制）</p>
                    <div className="space-y-1">
                      {(pack.publish.titles || []).map((t, i) => (
                        <button
                          key={i}
                          className="block w-full rounded-lg border border-gray-100 px-3 py-2 text-left hover:bg-gray-50"
                          onClick={() => copyText(toast, t, '标题已复制')}
                        >
                          {i + 1}. {t}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-gray-400">话题标签</span>
                    {(pack.publish.tags || []).map((t) => (
                      <span key={t} className="badge bg-brand-50 text-brand-700">{t}</span>
                    ))}
                    <button
                      className="text-xs text-brand-600 hover:underline"
                      onClick={() => copyText(toast, (pack.publish.tags || []).join(' '), '标签已复制')}
                    >
                      复制全部
                    </button>
                  </div>
                  {pack.publish.cover_text && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-400">封面大字</span>
                      <span className="rounded-lg bg-gray-900 px-3 py-1 text-lg font-bold text-amber-300">
                        {pack.publish.cover_text}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
