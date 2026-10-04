import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useToast } from '../toast'
import { StatusBadge } from './Today'

function HotspotRow({ h }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const reactivate = useMutation({
    mutationFn: () => api.post(`/api/hotspots/${h.id}/reactivate`),
    onSuccess: () => {
      toast('已恢复为待处理', 'success')
      qc.invalidateQueries()
    },
  })
  return (
    <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3 last:border-0">
      <span className={`badge shrink-0 ${h.is_must ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-500'}`}>
        {h.score}分
      </span>
      <div className="min-w-0 flex-1 cursor-pointer" onClick={() => navigate(`/hotspots/${h.id}`)}>
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{h.title}</span>
          {h.sources_count >= 2 && (
            <span className="badge shrink-0 bg-emerald-50 text-emerald-700">✓{h.sources_count}来源</span>
          )}
          {h.sequel_of && <span className="badge shrink-0 bg-purple-50 text-purple-700">📌续集</span>}
        </div>
        {h.why && <p className="mt-0.5 truncate text-xs text-gray-500">{h.why}</p>}
      </div>
      <span className="hidden shrink-0 text-xs text-gray-400 sm:inline">{h.day.slice(5)}</span>
      <StatusBadge status={h.status} />
      {h.status === 'expired' ? (
        <button className="btn-ghost shrink-0 text-xs" onClick={() => reactivate.mutate()}>
          恢复
        </button>
      ) : (
        <Link to={`/hotspots/${h.id}`} className="btn-ghost shrink-0 text-xs">
          {h.status === 'pending' ? '做这条 →' : '查看'}
        </Link>
      )}
    </div>
  )
}

export default function Hotspots() {
  // 默认"今日活跃"：与首页必做同一池子（昨日至今更新过、未过时）
  const [tab, setTab] = useState('active')
  const status = tab === 'active' ? '' : tab
  const { data, isLoading } = useQuery({
    queryKey: ['hotspots', tab],
    queryFn: () =>
      api.get('/api/hotspots?' + new URLSearchParams(tab === 'active' ? { active: true } : { status })),
  })

  const byDay = {}
  for (const h of data?.hotspots || []) {
    ;(byDay[h.day] = byDay[h.day] || []).push(h)
  }
  const days = Object.keys(byDay).sort().reverse()

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">热点选题</h1>
      <div className="flex flex-wrap gap-2">
        {[
          ['active', '今日活跃'],
          ['pending', '待处理'],
          ['packed', '已出素材'],
          ['shot', '已拍'],
          ['expired', '已过时'],
        ].map(([v, label]) => (
          <button key={v} className={`chip ${status === v ? 'chip-on' : 'chip-off'}`} onClick={() => setStatus(v)}>
            {label}
          </button>
        ))}
      </div>

      <div className="card overflow-hidden">
        {isLoading && <div className="p-10 text-center text-gray-400">加载中…</div>}
        {!isLoading && data && data.hotspots.length === 0 && (
          <div className="p-12 text-center text-gray-400">
            还没有热点选题。回首页点「刷新」，AI 会聚合出今日热点。
          </div>
        )}
        {!isLoading &&
          days.map((day) => (
            <div key={day}>
              <div className="bg-gray-50 px-4 py-1.5 text-xs font-medium text-gray-500">{day}</div>
              {byDay[day].map((h) => <HotspotRow key={h.id} h={h} />)}
            </div>
          ))}
      </div>
    </div>
  )
}
