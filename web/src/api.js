import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  get: (url) => request(url),
  post: (url, body) => request(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: (url, body) => request(url, { method: 'PUT', body: JSON.stringify(body) }),
  patch: (url, body) => request(url, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (url, body) => request(url, { method: 'DELETE', body: body === undefined ? undefined : JSON.stringify(body) }),
  upload: async (url, file) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(url, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(`上传失败 ${res.status}`)
    return res.json()
  },
}

/** 启动长任务并轮询进度，onJob 每次轮询回调（更新进度条）。 */
export function runJob(mutation, { onJob, pollMs = 1200 } = {}) {
  return new Promise((resolve, reject) => {
    const tick = async (jobId) => {
      try {
        const job = await api.get(`/api/jobs/${jobId}`)
        onJob && onJob(job)
        if (job.status === 'done') return resolve(job.result)
        if (job.status === 'error') return reject(new Error(job.error || '任务失败'))
        setTimeout(() => tick(jobId), pollMs)
      } catch (e) {
        reject(e)
      }
    }
    mutation.mutate(undefined, {
      onSuccess: (data) => tick(data.job_id),
      onError: reject,
    })
  })
}

/** 后台任务场景（可离开页面）：发完请求只弹 toast，不等待。 */
export function fireJob(mutation, { onDone, onError, toast, doneMessage } = {}) {
  mutation.mutate(undefined, {
    onSuccess: (data) => {
      const jobId = data.job_id
      const poll = async () => {
        try {
          const job = await api.get(`/api/jobs/${jobId}`)
          if (job.status === 'running') return setTimeout(poll, 2000)
          if (job.status === 'error') {
            toast(job.error || '任务失败', 'error')
            onError && onError(job)
          } else {
            toast(typeof doneMessage === 'function' ? doneMessage(job.result) : doneMessage || '任务完成', 'success')
            onDone && onDone(job.result)
          }
        } catch (e) {
          onError && onError(e)
        }
      }
      setTimeout(poll, 800)
    },
    onError: (e) => toast(e.message || '任务启动失败', 'error'),
  })
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: () => api.get('/api/health'), refetchInterval: 30000 })
}

export function useRefresh(toast, onDone) {
  const qc = useQueryClient()
  const mutation = useMutation({ mutationFn: () => api.post('/api/refresh') })
  const [job, setJob] = useState(null)
  const toastRef = useRef(toast)
  toastRef.current = toast
  const start = () => {
    if (mutation.isPending || job?.status === 'running') return
    runJob(mutation, {
      onJob: setJob,
      pollMs: 1500,
    })
      .then(async (result) => {
        await qc.invalidateQueries()
        toastRef.current(result?.toast || '刷新完成', 'success')
        onDone && onDone(result)
      })
      .catch((e) => toastRef.current(e.message || '刷新失败', 'error'))
      .finally(() => setJob(null))
  }
  return { start, job, running: mutation.isPending || job?.status === 'running' }
}

/** 简易轮询：任务进行中每 pollMs 重取一次 query。 */
export function usePollWhile(active, ms = 2000) {
  const qc = useQueryClient()
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => qc.invalidateQueries(), ms)
    return () => clearInterval(t)
  }, [active, ms, qc])
}
