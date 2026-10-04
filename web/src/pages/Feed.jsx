import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useToast } from '../toast'

const CATEGORIES = ['大模型', 'AI产品', '科技大事件']
const SOURCE_TYPES = [
  { value: 'official', label: '官方' },
  { value: 'media', label: '媒体' },
  { value: 'overseas', label: '海外' },
  { value: 'trending', label: '热榜' },
]

export function SourceBadge({ type }) {
  const map = {
    official: ['官方', 'bg-blue-100 text-blue-700'],
    media: ['媒体', 'bg-emerald-100 text-emerald-700'],
    overseas: ['海外', 'bg-purple-100 text-purple-700'],
    trending: ['热榜', 'bg-orange-100 text-orange-700'],
  }
  const [label, cls] = map[type] || [type, 'bg-gray-100 text-gray-600']
  return <span className={`badge ${cls}`}>{label}</span>
}

function relTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const diff = Math.floor((Date.now() - d.getTime()) / 1000)
  if (diff < 3600) return `${Math.max(1, Math.floor(diff / 60))}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  const days = Math.floor(diff / 86400)
  if (days < 30) return `${days}天前`
  return `${Math.floor(days / 30)}个月前`
}

function dateLabel(day) {
  const [y, m, d] = (day || '').split('-')
  return `${Number(m)}月${Number(d)}日`
}

/** EchoBird 风格行：大标题 + 来源 · 相对时间，点开原地展开详情。 */
function FeedRow({ item, onOpen }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const toggle = useMutation({
    mutationFn: (patch) => api.patch(`/api/items/${item.id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['feed'] }),
  })
  const read = !!item.is_read

  return (
    <div className={`group border-b border-gray-100 px-2 py-4 transition last:border-0 ${read ? 'opacity-50' : ''}`}>
      <div className="cursor-pointer" onClick={() => { setOpen(!open); if (!read) toggle.mutate({ is_read: true }) }}>
        <div className="flex items-start gap-3">
          <h3 className={`flex-1 text-[15px] font-medium leading-6 ${read ? 'text-gray-500' : 'text-gray-900'}`}>
            {item.title}
          </h3>
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="mt-1 shrink-0 text-gray-300 transition group-hover:text-brand-600"
            title="打开原文"
          >
            ↗
          </a>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-400">
          {item.source_name && <span>{item.source_name}</span>}
          <span>·</span>
          <span>{relTime(item.fetched_at)}</span>
          <span>·</span>
          <SourceBadge type={item.source_type} />
          {item.event_score >= 80 && (
            <span className="badge bg-amber-50 text-amber-600">🔥 热度{item.event_score}</span>
          )}
          {item.hotspot_title && (
            <Link
              to={`/hotspots/${item.hotspot_id}`}
              className="text-brand-500 hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              ·热点卡 →
            </Link>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-2 space-y-2 border-l-2 border-brand-100 pl-3 text-sm text-gray-700">
          <p>{item.summary || '（暂无摘要）'}</p>
          {item.summary_zh && (
            <p className="text-gray-500">
              <span className="text-xs text-purple-600">译</span> {item.summary_zh}
            </p>
          )}
          <div className="flex items-center gap-3 pt-1">
            <a href={item.url} target="_blank" rel="noreferrer" className="text-sm text-brand-600 hover:underline">
              ↗ 跳原文
            </a>
            <button
              className={`text-sm ${item.is_starred ? 'text-amber-500' : 'text-gray-400 hover:text-amber-500'}`}
              title="收藏后会出现在首页「今日待办」"
              onClick={(e) => {
                e.stopPropagation()
                toggle.mutate({ is_starred: !item.is_starred })
                if (!item.is_starred) toast('已收藏，加入首页「今日待办」', 'success')
              }}
            >
              {item.is_starred ? '★ 已收藏' : '☆ 收藏'}
            </button>
            <button
              className="text-sm text-gray-400 hover:text-gray-600"
              onClick={(e) => { e.stopPropagation(); toggle.mutate({ is_read: !read }) }}
            >
              {read ? '标为未读' : '标为已读'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** 历史归档树：年 → 月 → 日（带条数），点击某天过滤。 */
function ArchiveTree({ days, activeDay, onPick }) {
  const tree = useMemo(() => {
    const years = {}
    for (const { day, n } of days || []) {
      const [y, m] = day.split('-')
      years[y] = years[y] || {}
      years[y][m] = years[y][m] || []
      years[y][m].push({ day, n })
    }
    return Object.entries(years)
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([y, months]) => ({
        y,
        count: Object.values(months).reduce((s, ds) => s + ds.reduce((x, d) => x + d.n, 0), 0),
        months: Object.entries(months)
          .sort((a, b) => b[0].localeCompare(a[0]))
          .map(([m, ds]) => ({ m, count: ds.reduce((x, d) => x + d.n, 0), days: ds })),
      }))
  }, [days])
  const [openYear, setOpenYear] = useState('')
  const [openMonth, setOpenMonth] = useState('')
  // 数据到位后默认展开最新年月
  useEffect(() => {
    if (days?.length) {
      setOpenYear(days[0].day.slice(0, 4))
      setOpenMonth(days[0].day.slice(0, 7))
    }
  }, [days?.length])

  return (
    <div className="text-sm">
      <h3 className="mb-2 font-semibold text-gray-700">历史归档</h3>
      <div className="space-y-1">
        {tree.map(({ y, count, months }) => {
          const yOpen = openYear === y
          return (
            <div key={y}>
              <button
                className="flex w-full items-center justify-between rounded px-2 py-1.5 hover:bg-gray-100"
                onClick={() => setOpenYear(yOpen ? '' : y)}
              >
                <span className="flex items-center gap-1 text-gray-700">
                  <span className="text-[10px] text-gray-400">{yOpen ? '▾' : '▸'}</span> {y}
                </span>
                <span className="text-xs text-gray-400">{count}</span>
              </button>
              {yOpen && (
                <div className="ml-3 border-l border-gray-100 pl-2">
                  {months.map(({ m, count, days: ds }) => {
                    const mOpen = openMonth === `${y}-${m}`
                    return (
                      <div key={m}>
                        <button
                          className="flex w-full items-center justify-between rounded px-2 py-1 text-xs hover:bg-gray-100"
                          onClick={() => setOpenMonth(mOpen ? '' : `${y}-${m}`)}
                        >
                          <span className="text-gray-600">{Number(m)}月</span>
                          <span className="text-gray-400">{count}</span>
                        </button>
                        {mOpen && (
                          <div className="ml-2 border-l border-gray-100 pl-2">
                            {ds.map(({ day, n }) => (
                              <button
                                key={day}
                                className={`flex w-full items-center justify-between rounded px-2 py-1 text-xs ${
                                  activeDay === day ? 'bg-brand-50 font-medium text-brand-700' : 'hover:bg-gray-100 text-gray-600'
                                }`}
                                onClick={() => onPick(day)}
                              >
                                <span>{Number(day.slice(8))}日</span>
                                <span className="text-gray-400">{n}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
        {tree.length === 0 && <p className="px-2 text-xs text-gray-400">还没有历史数据</p>}
      </div>
    </div>
  )
}

export default function Feed() {
  const toast = useToast()
  const [view, setView] = useState('yesterday') // yesterday | today | archive
  const [archiveDay, setArchiveDay] = useState('')
  const [category, setCategory] = useState('')
  const [sourceType, setSourceType] = useState('')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [starredOnly, setStarredOnly] = useState(false)
  const [q, setQ] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [region, setRegion] = useState('')

  const yesterday = useMemo(() => {
    const d = new Date(Date.now() - 86400000)
    return d.toLocaleDateString('sv-SE')
  }, [])
  const today = useMemo(() => new Date().toLocaleDateString('sv-SE'), [])

  const day = view === 'yesterday' ? yesterday : view === 'today' ? today : archiveDay
  const sort = view === 'yesterday' || view === 'archive' ? 'hot' : ''
  const viewLabel =
    view === 'yesterday' ? '昨日最火' : view === 'today' ? '今天' : `归档 · ${dateLabel(archiveDay)}`

  const params = useMemo(
    () => ({ category, source_type: sourceType, unread: unreadOnly, starred: starredOnly, q, day, sort, region, limit: 200 }),
    [category, sourceType, unreadOnly, starredOnly, q, day, sort, region],
  )
  const { data, isLoading } = useQuery({
    queryKey: ['feed', params],
    queryFn: () => api.get('/api/feed?' + new URLSearchParams(params)),
  })
  const archiveQuery = useQuery({ queryKey: ['archive'], queryFn: () => api.get('/api/feed/archive') })

  const readAll = useMutation({
    mutationFn: () => api.post('/api/feed/read-all?' + new URLSearchParams({ category, source_type: sourceType, q, day, region })),
    onSuccess: (r) => {
      toast(r.marked > 0 ? `已把当前范围内 ${r.marked} 条标为已读` : '当前范围内没有未读', 'success')
      useQueryClient().invalidateQueries()
    },
  })
  const qc = useQueryClient()

  const pickArchive = (day) => {
    setArchiveDay(day)
    setView('archive')
  }

  return (
    <div className="flex gap-8">
      {/* 主列表 */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-xl font-bold">AI 资讯</h1>
            <p className="text-sm text-gray-500">
              {viewLabel}
              {data && ` · ${data.total} 条`}
            </p>
          </div>
          <button className="btn-ghost text-xs" onClick={() => readAll.mutate()} disabled={readAll.isPending}>
            全部标为已读
          </button>
        </div>

        {/* 视图切换 + 国内/国外 */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {[
            ['yesterday', '昨日最火'],
            ['today', '今天'],
          ].map(([v, label]) => (
            <button key={v} className={`chip ${view === v ? 'chip-on' : 'chip-off'}`} onClick={() => setView(v)}>
              {label}
            </button>
          ))}
          <span className="mx-1 h-4 w-px bg-gray-200" />
          {[
            ['', '全部'],
            ['国内', '国内'],
            ['海外', '国外'],
          ].map(([v, label]) => (
            <button key={v || 'all'} className={`chip ${region === v ? 'chip-on' : 'chip-off'}`} onClick={() => setRegion(v)}>
              {label}
            </button>
          ))}
          {view === 'archive' && archiveDay && (
            <span className="chip chip-on">
              {dateLabel(archiveDay)}
            </span>
          )}
        </div>

        {/* 可折叠筛选 */}
        <div className="mt-3">
          <button
            className="text-xs text-gray-400 hover:text-gray-600"
            onClick={() => setFiltersOpen(!filtersOpen)}
          >
            {filtersOpen ? '收起筛选 ▾' : '筛选 / 搜索 ▸'}
          </button>
          {filtersOpen && (
            <div className="mt-2 space-y-2">
              <input
                className="input"
                placeholder="搜索标题或摘要…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <div className="flex flex-wrap items-center gap-2">
                <button className={`chip ${!category ? 'chip-on' : 'chip-off'}`} onClick={() => setCategory('')}>
                  全部
                </button>
                {CATEGORIES.map((c) => (
                  <button
                    key={c}
                    className={`chip ${category === c ? 'chip-on' : 'chip-off'}`}
                    onClick={() => setCategory(category === c ? '' : c)}
                  >
                    {c}
                  </button>
                ))}
                <span className="mx-1 h-4 w-px bg-gray-200" />
                {SOURCE_TYPES.map((s) => (
                  <button
                    key={s.value}
                    className={`chip ${sourceType === s.value ? 'chip-on' : 'chip-off'}`}
                    onClick={() => setSourceType(sourceType === s.value ? '' : s.value)}
                  >
                    {s.label}
                  </button>
                ))}
                <span className="mx-1 h-4 w-px bg-gray-200" />
                <button
                  className={`chip ${unreadOnly ? 'chip-on' : 'chip-off'}`}
                  onClick={() => setUnreadOnly(!unreadOnly)}
                >
                  ☑ 只看未读
                </button>
                <button
                  className={`chip ${starredOnly ? 'chip-on' : 'chip-off'}`}
                  onClick={() => setStarredOnly(!starredOnly)}
                >
                  ☆ 收藏
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 列表 */}
        <div className="card mt-3 overflow-hidden">
          {isLoading && <div className="p-10 text-center text-gray-400">加载中…</div>}
          {!isLoading && data && data.items.length === 0 && (
            <div className="p-12 text-center text-gray-400">
              这一天没有符合条件的资讯。
            </div>
          )}
          {!isLoading && data && data.items.map((item) => <FeedRow key={item.id} item={item} />)}
        </div>
      </div>

      {/* 右侧：历史归档 */}
      <aside className="hidden w-60 shrink-0 lg:block">
        <div className="card sticky top-20 p-4">
          <ArchiveTree days={archiveQuery.data?.days} activeDay={view === 'archive' ? archiveDay : ''} onPick={pickArchive} />
          <p className="mt-3 border-t border-gray-100 pt-2 text-xs text-gray-400">
            点日期查看当天全部资讯
          </p>
        </div>
      </aside>
    </div>
  )
}
