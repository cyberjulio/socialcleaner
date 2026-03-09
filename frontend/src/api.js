const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

export const api = {
  // Auth
  connect: (platform, cookies) =>
    request('/auth/connect', {
      method: 'POST',
      body: JSON.stringify({ platform, cookies }),
    }),
  getSessions: () => request('/auth/sessions'),
  deleteSession: (id) => request(`/auth/sessions/${id}`, { method: 'DELETE' }),

  // Tasks
  createTask: (sessionId, targetType) =>
    request('/tasks', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, target_type: targetType }),
    }),
  getTasks: () => request('/tasks'),
  getTask: (id) => request(`/tasks/${id}`),
  updateTask: (id, status) =>
    request(`/tasks/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  deleteTask: (id) => request(`/tasks/${id}`, { method: 'DELETE' }),

  // SSE
  streamTask: (taskId, onEvent) => {
    const source = new EventSource(`${BASE}/tasks/${taskId}/stream`)
    source.addEventListener('task_status', (e) => onEvent('task_status', JSON.parse(e.data)))
    source.addEventListener('scan_progress', (e) => onEvent('scan_progress', JSON.parse(e.data)))
    source.addEventListener('scan_complete', (e) => onEvent('scan_complete', JSON.parse(e.data)))
    source.addEventListener('item_deleted', (e) => onEvent('item_deleted', JSON.parse(e.data)))
    source.addEventListener('item_failed', (e) => onEvent('item_failed', JSON.parse(e.data)))
    source.addEventListener('rate_limited', (e) => onEvent('rate_limited', JSON.parse(e.data)))
    source.addEventListener('checkpoint_required', (e) => onEvent('checkpoint_required', JSON.parse(e.data)))
    source.addEventListener('log', (e) => onEvent('log', JSON.parse(e.data)))
    return source
  },
}
