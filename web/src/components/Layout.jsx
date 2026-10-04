import { NavLink, useLocation } from 'react-router-dom'
import { useHealth, useRefresh } from '../api'
import { useToast } from '../toast'

const NAV = [
  { to: '/', label: '今日', end: true },
  { to: '/hotspots', label: '热点' },
  { to: '/feed', label: '资讯' },
  { to: '/styles', label: '拆解库' },
  { to: '/settings', label: '设置' },
]

function RefreshButton() {
  const toast = useToast()
  const { data: health } = useHealth()
  const { start, running, job } = useRefresh(toast)
  const location = useLocation()
  const enabled = !!health?.features?.refresh
  const progress = job?.total ? `${Math.min(job.done ?? 0, job.total)}/${job.total}` : ''
  const pct = job?.total ? Math.round(((job.done ?? 0) / job.total) * 100) : 0

  if (!enabled && !['/', '/feed', '/hotspots'].includes(location.pathname)) return null

  return (
    <div className="flex items-center gap-3">
      {health?.last_refresh_at && (
        <span className="hidden text-xs text-gray-500 md:inline">
          上次更新 {health.last_refresh_at.slice(5, 16).replace('T', ' ')}
        </span>
      )}
      <div className="relative">
        <button
          onClick={start}
          disabled={!enabled || running}
          className="btn-primary"
          title={enabled ? '抓取今日资讯' : '抓取源尚未接入'}
        >
          {running ? (
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/40 border-t-white" />
          ) : (
            <span>⟳</span>
          )}
          {running ? `抓取中 ${progress}` : '刷新'}
        </button>
        {running && pct > 0 && (
          <div
            className="absolute -bottom-1 left-0 h-0.5 rounded bg-brand-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
    </div>
  )
}

export default function Layout({ children }) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-gray-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <div className="flex items-center gap-8">
            <NavLink to="/" className="flex items-center gap-2 text-lg font-bold">
              <span className="text-xl">🎙</span>
              <span>AI口播工作台</span>
            </NavLink>
            <nav className="flex items-center gap-1">
              {NAV.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    `rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                      isActive ? 'bg-brand-50 text-brand-700' : 'text-gray-600 hover:bg-gray-100'
                    }`
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <RefreshButton />
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  )
}
