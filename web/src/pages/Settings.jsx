import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../toast'

const MODELS = [
  { value: 'glm-4-flash', label: 'glm-4-flash（默认，快且便宜）' },
  { value: 'glm-4-air', label: 'glm-4-air（更强，稍贵）' },
  { value: 'glm-4-plus', label: 'glm-4-plus（最强，最贵）' },
]
const TONES = ['轻松接地气', '快节奏资讯播报', '理性分析派']

export default function Settings() {
  const toast = useToast()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['settings'], queryFn: () => api.get('/api/settings') })

  const [keyInput, setKeyInput] = useState('')
  const [keyDirty, setKeyDirty] = useState(false)
  const [model, setModel] = useState('glm-4-flash')
  const [sources, setSources] = useState({})
  const [tone, setTone] = useState('轻松接地气')
  const [extra, setExtra] = useState('')
  const [testResult, setTestResult] = useState(null)

  useEffect(() => {
    if (!data) return
    setModel(data.model)
    setSources(Object.fromEntries(data.sources.map((s) => [s.key, s.enabled])))
    setTone(data.style_pref?.tone || '轻松接地气')
    setExtra(data.style_pref?.extra || '')
  }, [data])

  const saveMutation = useMutation({ mutationFn: (patch) => api.put('/api/settings', patch) })
  const testMutation = useMutation({ mutationFn: () => api.post('/api/settings/test') })

  const save = async () => {
    const patch = { model, sources, style_pref: { tone, extra } }
    if (keyDirty && keyInput.trim()) patch.api_key = keyInput.trim()
    try {
      await saveMutation.mutateAsync(patch)
      setKeyInput('')
      setKeyDirty(false)
      setTestResult(null)
      await qc.invalidateQueries()
      toast('设置已保存', 'success')
    } catch (e) {
      toast(e.message || '保存失败', 'error')
    }
  }

  const testConnection = async () => {
    setTestResult({ status: 'loading' })
    try {
      // 输入框里有未保存的新 key：先保存再测试，一步完成
      if (keyDirty && keyInput.trim()) {
        await api.put('/api/settings', { api_key: keyInput.trim() })
        setKeyInput('')
        setKeyDirty(false)
        await qc.invalidateQueries()
      }
      const r = await testMutation.mutateAsync()
      setTestResult({ status: r.ok ? 'ok' : 'fail', message: r.message })
      if (r.ok) toast('API key 已保存并验证可用', 'success')
    } catch (e) {
      setTestResult({ status: 'fail', message: e.message || '测试请求失败' })
    }
  }

  if (!data) return <div className="py-20 text-center text-gray-400">加载中…</div>

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-xl font-bold">设置</h1>

      {/* AI 能力 */}
      <section className="card p-5">
        <h2 className="mb-4 font-semibold">AI 能力</h2>
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-gray-600">智谱 API Key</label>
            <div className="flex gap-2">
              <input
                type="password"
                className="input"
                placeholder={data.has_api_key ? `已配置（${data.api_key_masked}），如需更换请粘贴新 key` : '请粘贴智谱 API key'}
                value={keyInput}
                onChange={(e) => {
                  setKeyInput(e.target.value)
                  setKeyDirty(true)
                }}
              />
              <button className="btn-ghost shrink-0" onClick={testConnection} disabled={testMutation.isPending}>
                {testMutation.isPending
                  ? '测试中…'
                  : keyDirty && keyInput.trim()
                    ? '保存并测试'
                    : '测试连接'}
              </button>
            </div>
            <p className="mt-1 text-xs text-gray-400">粘贴后点「保存并测试」会自动保存并立即验证，无需再点下方保存</p>
            {testResult && testResult.status !== 'loading' && (
              <p className={`mt-2 text-sm ${testResult.status === 'ok' ? 'text-emerald-600' : 'text-red-600'}`}>
                {testResult.status === 'ok' ? '✓ ' : '✗ '}
                {testResult.message}
              </p>
            )}
          </div>
          <div className="max-w-xs">
            <label className="mb-1 block text-sm text-gray-600">模型</label>
            <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
              {MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
        </div>
      </section>

      {/* 抓取源 */}
      <section className="card p-5">
        <h2 className="mb-1 font-semibold">抓取源</h2>
        <p className="mb-4 text-sm text-gray-500">每个源独立开关，不稳定的源可临时关闭</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {data.sources.map((s) => (
            <label
              key={s.key}
              className="flex cursor-pointer items-center justify-between rounded-lg border border-gray-200 px-3 py-2.5 hover:bg-gray-50"
            >
              <span className="flex items-center gap-2 text-sm">
                <span
                  className={`badge ${
                    s.type === 'official'
                      ? 'bg-blue-100 text-blue-700'
                      : s.type === 'media'
                        ? 'bg-emerald-100 text-emerald-700'
                        : s.type === 'overseas'
                          ? 'bg-purple-100 text-purple-700'
                          : 'bg-orange-100 text-orange-700'
                  }`}
                >
                  {{ official: '官方', media: '媒体', overseas: '海外', trending: '热榜' }[s.type]}
                </span>
                {s.name}
              </span>
              <input
                type="checkbox"
                className="h-4 w-4 accent-brand-600"
                checked={sources[s.key] ?? true}
                onChange={(e) => setSources((prev) => ({ ...prev, [s.key]: e.target.checked }))}
              />
            </label>
          ))}
        </div>
      </section>

      {/* 写稿偏好 */}
      <section className="card p-5">
        <h2 className="mb-1 font-semibold">写稿偏好</h2>
        <p className="mb-4 text-sm text-gray-500">影响 AI 口播稿和热点文案的写法，保存后即时生效</p>
        <div className="space-y-4">
          <div className="max-w-xs">
            <label className="mb-1 block text-sm text-gray-600">口播风格</label>
            <select className="input" value={tone} onChange={(e) => setTone(e.target.value)}>
              {TONES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm text-gray-600">补充要求（可选）</label>
            <textarea
              className="input min-h-20"
              placeholder='例如：别用"家人们"开头；涉及价格必须带出处'
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
            />
          </div>
        </div>
      </section>

      {/* 数据与备份 */}
      <DataSection toast={toast} qc={qc} />

      <div className="flex justify-end">
        <button className="btn-primary px-6" onClick={save} disabled={saveMutation.isPending}>
          {saveMutation.isPending ? '保存中…' : '保存设置'}
        </button>
      </div>
    </div>
  )
}

function DataSection({ toast, qc }) {
  const fileRef = useRef(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const { data } = useQuery({
    queryKey: ['backup'],
    queryFn: () => api.get('/api/backup/list'),
  })

  const exportBackup = async () => {
    try {
      const res = await fetch('/api/backup/export', { method: 'POST' })
      if (!res.ok) throw new Error('导出失败')
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = res.headers.get('content-disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'workbench-backup.zip'
      a.click()
      URL.revokeObjectURL(a.href)
      toast('备份已导出', 'success')
    } catch (e) {
      toast(e.message || '导出失败', 'error')
    }
  }

  const importBackup = useMutation({
    mutationFn: (file) => api.upload('/api/backup/import', file),
    onSuccess: (r) => {
      toast(`已恢复 ${r.restored.length} 个文件（恢复前已自动备份当前数据），请重启应用生效`, 'success')
      qc.invalidateQueries()
    },
    onError: (e) => toast(e.message || '恢复失败', 'error'),
  })

  const clearData = useMutation({
    mutationFn: () => api.post('/api/data/clear', { confirm: true }),
    onSuccess: (r) => {
      toast(`已清空全部数据（清空前已自动备份，移除 ${r.removed_images} 张图片缓存）`, 'success')
      setConfirmClear(false)
      qc.invalidateQueries()
    },
    onError: (e) => toast(e.message || '清空失败', 'error'),
  })

  const meta = data?.meta
  return (
    <section className="card p-5">
      <h2 className="mb-1 font-semibold">数据与备份</h2>
      <p className="mb-4 text-sm text-gray-500">
        数据保存在本机 data 目录；每次启动自动备份，滚动保留最近 7 份。
        {meta && ` 当前：${meta.items} 条资讯 · ${meta.hotspots} 个热点 · ${meta.packs} 个素材包 · ${meta.styles} 个模板`}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-ghost" onClick={exportBackup}>⬇ 导出备份（zip）</button>
        <button className="btn-ghost" onClick={() => fileRef.current?.click()} disabled={importBackup.isPending}>
          {importBackup.isPending ? '恢复中…' : '⬆ 导入恢复'}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) importBackup.mutate(f)
            e.target.value = ''
          }}
        />
        <span className="mx-1 h-4 w-px bg-gray-200" />
        <button className="btn text-red-600 hover:bg-red-50" onClick={() => setConfirmClear(true)}>
          清空全部数据
        </button>
      </div>
      {data?.backups?.length > 0 && (
        <p className="mt-3 text-xs text-gray-400">
          自动备份 {data.backups.length} 份，最新：{data.backups[0].name}
        </p>
      )}

      {confirmClear && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
          <div className="card w-full max-w-sm space-y-3 p-6">
            <h3 className="font-bold text-red-600">确认清空全部数据？</h3>
            <p className="text-sm text-gray-500">
              将删除全部资讯、热点、素材包、模板和图片缓存（API key 等设置保留）。清空前会自动备份一次，不可轻易撤销。
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmClear(false)}>取消</button>
              <button className="btn-danger" onClick={() => clearData.mutate()} disabled={clearData.isPending}>
                {clearData.isPending ? '清空中…' : '确认清空'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
