import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fireJob } from '../api'
import { useToast } from '../toast'

export function StatusBadge({ status }) {
  const map = {
    pending: ['待处理', 'bg-gray-100 text-gray-600'],
    packed: ['已出素材', 'bg-blue-100 text-blue-700'],
    shot: ['已拍 ✅', 'bg-emerald-100 text-emerald-700'],
  }
  const [label, cls] = map[status] || [status, 'bg-gray-100 text-gray-600']
  return <span className={`badge ${cls}`}>{label}</span>
}

export function CoverImage({ src, className = '' }) {
  const [err, setErr] = useState(false)
  if (!src || err) {
    return (
      <div className={`flex items-center justify-center bg-gradient-to-br from-indigo-100 to-purple-100 ${className}`}>
        <span className="text-2xl">🎙</span>
      </div>
    )
  }
  return <img src={src} className={`object-cover ${className}`} onError={() => setErr(true)} />
}

function MustCard({ h, onExpire }) {
  const navigate = useNavigate()
  const today = new Date().toLocaleDateString('sv-SE') // 本地日期 YYYY-MM-DD
  return (
    <div className="card overflow-hidden transition hover:shadow-md">
      <CoverImage src={h.cover} className="h-36 w-full" />
      <div className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold leading-6">{h.title}</h3>
          <span className="badge shrink-0 bg-amber-100 text-amber-700">{h.suggested_length}</span>
        </div>
        {h.why && <p className="text-sm text-gray-600">{h.why}</p>}
        <div className="flex flex-wrap items-center gap-2">
          {h.day && h.day !== today && (
            <span className="badge bg-gray-100 text-gray-500">{h.day.slice(5)} 递补</span>
          )}
          {h.sources_count >= 2 && (
            <span className="badge bg-emerald-50 text-emerald-700">✓{h.sources_count}个独立来源确认</span>
          )}
          {h.sequel_of && <span className="badge bg-purple-50 text-purple-700">📌 可做续集</span>}
          <StatusBadge status={h.status} />
        </div>
        {h.angles?.length > 0 && (
          <p className="text-xs text-gray-500">切入角度：{h.angles.join(' / ')}</p>
        )}
        <div className="mt-1 flex gap-2">
          <button className="btn-primary flex-1" onClick={() => navigate(`/hotspots/${h.id}`)}>
            {h.status === 'pending' ? '做这条 →' : '查看素材包 →'}
          </button>
          <button
            className="btn-ghost shrink-0 text-xs text-gray-400"
            title="过时了/不打算做：移出必做，不再递补"
            onClick={() => onExpire && onExpire(h)}
          >
            过时了
          </button>
        </div>
      </div>
    </div>
  )
}

function TodoSection({ todos }) {
  const qc = useQueryClient()
  const toast = useToast()
  const navigate = useNavigate()
  const doneMutation = useMutation({
    mutationFn: (id) => api.patch(`/api/items/${id}`, { todo_done: true }),
    onSuccess: () => qc.invalidateQueries(),
  })
  const promoteMutation = useMutation({
    mutationFn: (id) => api.post(`/api/items/${id}/promote`),
    onSuccess: (r) => {
      toast('已转为选题卡，去生成素材包吧', 'success')
      qc.invalidateQueries()
      navigate(`/hotspots/${r.hotspot_id}`)
    },
  })
  if (!todos?.length) return null
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold">今日待办（{todos.length}）—— 收藏的资讯，可转为选题</h2>
        <Link to="/hotspots?view=feed" className="text-xs text-brand-600 hover:underline">去资讯页收藏更多 →</Link>
      </div>
      <div className="space-y-1">
        {todos.map((t) => (
          <div key={t.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50">
            <input
              type="checkbox"
              className="h-4 w-4 shrink-0 accent-brand-600"
              title="标记完成"
              onChange={() => {
                doneMutation.mutate(t.id)
                toast('待办已完成', 'success')
              }}
            />
            <a href={t.url} target="_blank" rel="noreferrer" className="min-w-0 flex-1 truncate text-sm hover:underline">
              {t.title}
            </a>
            <span className="shrink-0 text-xs text-gray-400">{t.source_name || ''}</span>
            <button
              className="btn-ghost shrink-0 text-xs"
              title="升级为选题卡，进入热点工作流"
              onClick={() => promoteMutation.mutate(t.id)}
            >
              转为选题 →
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function FirstRunGuide() {
  return (
    <div className="card mx-auto max-w-xl p-8 text-center">
      <div className="text-4xl">🎙</div>
      <h2 className="mt-3 text-lg font-bold">三步开始使用</h2>
      <div className="mx-auto mt-6 max-w-sm space-y-3 text-left text-sm">
        <div className="flex gap-3 rounded-lg border border-gray-200 p-3">
          <span className="font-bold text-brand-600">1</span>
          <span>
            去<Link className="text-brand-600 hover:underline" to="/settings">设置</Link>页填入智谱 API key 并保存
          </span>
        </div>
        <div className="flex gap-3 rounded-lg border border-gray-200 p-3">
          <span className="font-bold text-brand-600">2</span>
          <span>回到本页点右上角「刷新」，抓取今日资讯</span>
        </div>
        <div className="flex gap-3 rounded-lg border border-gray-200 p-3">
          <span className="font-bold text-brand-600">3</span>
          <span>看「今日必做」选题，点「做这条」生成口播稿和配图</span>
        </div>
      </div>
    </div>
  )
}

export default function Today() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['today'], queryFn: () => api.get('/api/today') })
  const [backupOpen, setBackupOpen] = useState(false)

  const reevalMutation = useMutation({ mutationFn: () => api.post('/api/today/reevaluate') })
  const expireMutation = useMutation({
    mutationFn: (id) => api.post(`/api/hotspots/${id}/expire`),
    onSuccess: (r) => {
      toast(r.backfilled > 0 ? '已移出，已自动补入新选题' : '已标记过时', 'success')
      qc.invalidateQueries()
    },
  })
  const onExpire = (h) => {
    if (window.confirm(`把「${h.title.slice(0, 20)}…」标记为过时？
将移出今日必做，且不再递补（热点库"已过时"里可恢复）。`)) {
      expireMutation.mutate(h.id)
    }
  }
  const reevaluate = () =>
    fireJob(reevalMutation, {
      toast,
      doneMessage: '重新评估完成',
      onDone: () => qc.invalidateQueries(),
    })

  if (isLoading) return <div className="py-20 text-center text-gray-400">加载中…</div>
  if (!data) return null

  if (!data.key_configured) {
    return (
      <div>
        <h1 className="mb-4 text-xl font-bold">今日</h1>
        <FirstRunGuide />
      </div>
    )
  }

  const dateStr = new Date().toLocaleDateString('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
  })

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold">今日必做</h1>
          <p className="text-sm text-gray-500">
            {dateStr}
            {data.last_refresh_at && ` · 上次更新 ${data.last_refresh_at.slice(11, 16)}`}
          </p>
        </div>
        <button className="btn-ghost text-sm" onClick={reevaluate} disabled={reevalMutation.isPending}>
          重新评估一次
        </button>
      </div>

      {/* 今日待办（收藏的资讯） */}
      <TodoSection todos={data.todos} />

      {data.must.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {data.must.map((h) => <MustCard key={h.id} h={h} onExpire={onExpire} />)}
        </div>
      ) : (
        data.empty_reason && (
          <div className="card p-8 text-center">
            <div className="text-3xl">🌤</div>
            <h3 className="mt-2 font-semibold">今天没有值得单独开一条视频的选题</h3>
            <p className="mx-auto mt-2 max-w-md text-sm text-gray-500">{data.empty_reason}</p>
            <div className="mt-4 flex justify-center gap-2">
              <button className="btn-ghost" onClick={reevaluate}>重新评估一次</button>
              <Link to="/hotspots?view=feed" className="btn-primary">去资讯流水逛逛 →</Link>
            </div>
          </div>
        )
      )}

    </div>
  )
}
