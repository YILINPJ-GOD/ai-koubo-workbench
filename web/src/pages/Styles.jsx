import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, runJob } from '../api'
import { useToast } from '../toast'

const TEARDOWN_LABELS = {
  hook_type: '钩子类型',
  structure: '结构骨架',
  rhythm: '节奏特征',
  golden_pattern: '金句模式',
  cta_type: '结尾CTA',
}

function TeardownView({ teardown }) {
  if (!teardown) return null
  return (
    <div className="space-y-1.5 text-sm">
      <p><span className="text-gray-400">{TEARDOWN_LABELS.hook_type}：</span>{teardown.hook_type || '—'}</p>
      <p>
        <span className="text-gray-400">{TEARDOWN_LABELS.structure}：</span>
        {(teardown.structure || []).join(' → ') || '—'}
      </p>
      <p><span className="text-gray-400">{TEARDOWN_LABELS.rhythm}：</span>{teardown.rhythm || '—'}</p>
      <p><span className="text-gray-400">{TEARDOWN_LABELS.golden_pattern}：</span>{teardown.golden_pattern || '—'}</p>
      <p><span className="text-gray-400">{TEARDOWN_LABELS.cta_type}：</span>{teardown.cta_type || '—'}</p>
    </div>
  )
}

function NewStyleModal({ onClose, toast }) {
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [link, setLink] = useState('')
  const [name, setName] = useState('')
  const [teardown, setTeardown] = useState(null)
  const [teardownText, setTeardownText] = useState('') // 可编辑的 JSON
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState('')
  const [extracting, setExtracting] = useState(false)
  const [extractProgress, setExtractProgress] = useState('')

  const tdMutation = useMutation({ mutationFn: () => api.post('/api/styles/teardown', { text }) })
  const extractMutation = useMutation({ mutationFn: () => api.post('/api/styles/extract-text', { link }) })

  const extractFromLink = () => {
    if (!link.trim()) {
      toast('先粘贴视频分享链接', 'error')
      return
    }
    setExtracting(true)
    setExtractProgress('准备提取…')
    runJob(extractMutation, { onJob: (job) => setExtractProgress(job.message || '提取中…'), pollMs: 2500 })
      .then((result) => {
        setText(result.text)
        toast('文案已提取，可直接修改后拆解', 'success')
      })
      .catch((e) => toast(e.message || '提取失败，可直接粘贴文案文字', 'error'))
      .finally(() => setExtracting(false))
  }
  const saveMutation = useMutation({
    mutationFn: () => api.post('/api/styles', { name, source_text: text, teardown: JSON.parse(teardownText) }),
  })

  const runTeardown = () => {
    if (text.trim().length < 30) {
      toast('文案太短（至少30字）', 'error')
      return
    }
    setRunning(true)
    setProgress('AI 正在拆解结构，约需半分钟…')
    runJob(tdMutation, { onJob: (job) => setProgress(job.message || '拆解中…'), pollMs: 2000 })
      .then((result) => {
        setTeardown(result)
        setTeardownText(JSON.stringify(result, null, 2))
      })
      .catch((e) => toast(e.message || '拆解失败', 'error'))
      .finally(() => setRunning(false))
  }

  const save = async () => {
    if (!name.trim()) {
      toast('先给模板起个名字', 'error')
      return
    }
    try {
      JSON.parse(teardownText)
    } catch {
      toast('拆解结果不是有效 JSON，请检查编辑内容', 'error')
      return
    }
    try {
      await saveMutation.mutateAsync()
      await qc.invalidateQueries()
      toast('模板已保存', 'success')
      onClose()
    } catch (e) {
      toast(e.message || '保存失败', 'error')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-6">
      <div className="card w-full max-w-2xl space-y-4 p-6">
        <h2 className="text-lg font-bold">拆解一篇口播</h2>

        {!teardown && (
          <div className="space-y-3">
            <p className="text-sm text-gray-500">
              两种方式：① 直接粘贴抖音/快手/B站视频分享链接，自动下载视频并转文字；② 直接粘贴文案文字。AI 只拆结构和节奏，不照搬内容。
            </p>
            <div className="rounded-lg border border-brand-100 bg-brand-50/40 p-3">
              <label className="mb-1 block text-sm text-gray-600">从视频链接提取（抖音分享链接即可）</label>
              <div className="flex gap-2">
                <input
                  className="input"
                  placeholder="粘贴分享链接，如 https://v.douyin.com/xxxx/"
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                />
                <button
                  className="btn-primary shrink-0"
                  onClick={extractFromLink}
                  disabled={extracting}
                >
                  {extracting ? '提取中…' : '提取文案'}
                </button>
              </div>
              {extracting && (
                <p className="mt-2 animate-pulse text-sm text-brand-600">
                  {extractProgress}（首次使用需下载语音模型约500MB，之后很快）
                </p>
              )}
            </div>
            <p className="text-xs text-gray-400">—— 或直接粘贴文案 ——</p>
            <textarea
              className="input min-h-40"
              placeholder="粘贴口播文案全文…"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            {running && <p className="animate-pulse text-sm text-brand-600">{progress}</p>}
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={onClose}>取消</button>
              <button className="btn-primary" onClick={runTeardown} disabled={running}>
                {running ? '拆解中…' : '开始拆解'}
              </button>
            </div>
          </div>
        )}

        {teardown && (
          <div className="space-y-3">
            <div className="rounded-lg border border-brand-100 bg-brand-50/50 p-4">
              <TeardownView teardown={teardown} />
            </div>
            <div>
              <label className="mb-1 block text-sm text-gray-600">模板名</label>
              <input
                className="input"
                placeholder="如：XX的悬念式开头"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-gray-600">
                拆解结果（可直接编辑，保存后可再改）
              </label>
              <textarea
                className="input min-h-36 font-mono text-xs"
                value={teardownText}
                onChange={(e) => setTeardownText(e.target.value)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => { setTeardown(null); setTeardownText('') }}>
                上一步
              </button>
              <button className="btn-primary" onClick={save} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? '保存中…' : '保存为模板'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function ApplyModal({ style, onClose }) {
  const navigate = useNavigate()
  const { data } = useQuery({
    queryKey: ['hotspots', 'today'],
    queryFn: () => api.get('/api/hotspots'),
  })
  const today = new Date().toISOString().slice(0, 10)
  const todays = (data?.hotspots || []).filter((h) => h.day === today || h.status === 'pending')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
      <div className="card w-full max-w-md space-y-3 p-6">
        <h2 className="text-lg font-bold">用「{style.name}」写一条</h2>
        {todays.length === 0 ? (
          <p className="text-sm text-gray-500">今天还没有热点选题，先去首页刷新。</p>
        ) : (
          <div className="max-h-64 space-y-1 overflow-y-auto">
            {todays.map((h) => (
              <button
                key={h.id}
                className="block w-full rounded-lg border border-gray-100 px-3 py-2 text-left text-sm hover:bg-gray-50"
                onClick={() => navigate(`/hotspots/${h.id}?style=${style.id}`)}
              >
                <span className="badge mr-2 bg-gray-100 text-gray-500">{h.score}分</span>
                {h.title}
              </button>
            ))}
          </div>
        )}
        <div className="flex justify-end">
          <button className="btn-ghost" onClick={onClose}>取消</button>
        </div>
      </div>
    </div>
  )
}

export default function Styles() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['styles'], queryFn: () => api.get('/api/styles') })
  const [modal, setModal] = useState(null) // 'new' | {apply: style} | {edit: style}
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [editName, setEditName] = useState('')

  const delMutation = useMutation({
    mutationFn: (id) => api.del(`/api/styles/${id}`),
    onSuccess: async () => {
      toast('模板已删除', 'success')
      setConfirmDelete(null)
      await qc.invalidateQueries()
    },
  })
  const renameMutation = useMutation({
    mutationFn: ({ id, name }) => api.put(`/api/styles/${id}`, { name }),
    onSuccess: async () => {
      toast('已改名', 'success')
      setConfirmDelete(null)
      await qc.invalidateQueries()
    },
  })

  const styles = data?.styles || []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold">拆解库</h1>
          <p className="text-sm text-gray-500">把别人的好口播拆成结构模板，生成素材包时套用。学骨架，不照搬。</p>
        </div>
        <button className="btn-primary" onClick={() => setModal('new')}>+ 拆解一篇口播</button>
      </div>

      <div className="card overflow-hidden">
        {isLoading && <div className="p-10 text-center text-gray-400">加载中…</div>}
        {!isLoading && styles.length === 0 && (
          <div className="p-12 text-center text-gray-400">
            还没有模板。找到一条你喜欢的口播视频，提取文案粘进来，AI 拆出五件套。
          </div>
        )}
        {styles.map((s) => (
          <div key={s.id} className="border-b border-gray-100 p-4 last:border-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{s.name}</span>
              <span className="badge bg-gray-100 text-gray-500">{s.teardown.hook_type}</span>
              {s.used_count > 0 && <span className="badge bg-brand-50 text-brand-700">已用{s.used_count}次</span>}
              <span className="ml-auto flex items-center gap-2">
                <button className="btn-ghost text-xs" onClick={() => setModal({ apply: s })}>套用写一条</button>
                <button
                  className="btn-ghost text-xs"
                  onClick={() => {
                    setConfirmDelete({ id: s.id, mode: 'rename' })
                    setEditName(s.name)
                  }}
                >
                  改名
                </button>
                <button
                  className="btn-ghost text-xs text-red-600"
                  onClick={() => setConfirmDelete({ id: s.id, mode: 'delete' })}
                >
                  删除
                </button>
              </span>
            </div>
            <div className="mt-2">
              <TeardownView teardown={s.teardown} />
            </div>
          </div>
        ))}
      </div>

      {/* 新建拆解 */}
      {modal === 'new' && <NewStyleModal onClose={() => setModal(null)} toast={toast} />}

      {/* 套用写一条 */}
      {modal?.apply && <ApplyModal style={modal.apply} onClose={() => setModal(null)} />}

      {/* 改名 / 删除确认 */}
      {confirmDelete?.mode === 'rename' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
          <div className="card w-full max-w-sm space-y-3 p-6">
            <h3 className="font-bold">修改模板名</h3>
            <input className="input" value={editName} onChange={(e) => setEditName(e.target.value)} />
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmDelete(null)}>取消</button>
              <button
                className="btn-primary"
                onClick={() => renameMutation.mutate({ id: confirmDelete.id, name: editName })}
              >
                保存
              </button>
            </div>
          </div>
        </div>
      )}
      {confirmDelete?.mode === 'delete' && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
          <div className="card w-full max-w-sm space-y-3 p-6">
            <h3 className="font-bold">确认删除这个模板？</h3>
            <p className="text-sm text-gray-500">删除后不可恢复。</p>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmDelete(null)}>取消</button>
              <button className="btn-danger" onClick={() => delMutation.mutate(confirmDelete.id)}>删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
