import { useState, useEffect, useRef } from 'react'
import { api } from '../api.js'

const STATUS_COLORS = {
  pending: 'bg-yellow-900 text-yellow-300',
  scanning: 'bg-blue-900 text-blue-300',
  running: 'bg-green-900 text-green-300',
  paused: 'bg-gray-700 text-gray-300',
  completed: 'bg-green-900/50 text-green-400',
  failed: 'bg-red-900 text-red-300',
}

export default function TaskCard({ task: initialTask, onRefresh, onDelete, compact }) {
  const [task, setTask] = useState(initialTask)
  const [notice, setNotice] = useState(null)
  const [logs, setLogs] = useState([])
  const [showLogs, setShowLogs] = useState(true)
  const [copied, setCopied] = useState(false)
  const [autoCloseIn, setAutoCloseIn] = useState(null)
  const sourceRef = useRef(null)
  const logEndRef = useRef(null)
  const autoCloseRef = useRef(null)

  useEffect(() => {
    setTask(initialTask)
  }, [initialTask])

  useEffect(() => {
    if (logEndRef.current) {
      logEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs])

  // Cancel auto-close countdown on unmount
  useEffect(() => {
    return () => {
      if (autoCloseRef.current) clearInterval(autoCloseRef.current)
    }
  }, [])

  useEffect(() => {
    const source = api.streamTask(task.id, (eventType, data) => {
      const ts = new Date().toLocaleTimeString()
      if (eventType === 'task_status') {
        setTask(prev => ({ ...prev, status: data.status }))
        setLogs(prev => [...prev, { ts, level: 'info', msg: `Status: ${data.status}${data.error ? ' — ' + data.error : ''}` }])
        if (['completed', 'failed'].includes(data.status)) {
          // Start 30s countdown, then refresh (which moves task to compact/history)
          setAutoCloseIn(30)
          autoCloseRef.current = setInterval(() => {
            setAutoCloseIn(prev => {
              if (prev <= 1) {
                clearInterval(autoCloseRef.current)
                autoCloseRef.current = null
                onRefresh()
                return null
              }
              return prev - 1
            })
          }, 1000)
        }
      } else if (eventType === 'scan_progress') {
        setTask(prev => ({ ...prev, total_items: data.found, status: 'scanning' }))
        setLogs(prev => [...prev, { ts, level: 'info', msg: `Scan progress: ${data.found} found` }])
      } else if (eventType === 'scan_complete') {
        setTask(prev => ({ ...prev, total_items: data.total }))
        setLogs(prev => [...prev, { ts, level: 'info', msg: `Scan complete: ${data.total} items` }])
      } else if (eventType === 'item_deleted') {
        setTask(prev => ({ ...prev, deleted: prev.deleted + 1 }))
        setLogs(prev => [...prev, { ts, level: 'ok', msg: `Deleted: ${data.platform_id}` }])
      } else if (eventType === 'item_failed') {
        setTask(prev => ({ ...prev, failed: prev.failed + 1 }))
        setLogs(prev => [...prev, { ts, level: 'error', msg: `Failed: ${data.item_id} — ${data.reason || ''}` }])
      } else if (eventType === 'rate_limited') {
        setNotice('Rate limited - backing off automatically')
        setLogs(prev => [...prev, { ts, level: 'warn', msg: data.message }])
      } else if (eventType === 'checkpoint_required') {
        setNotice('Platform requires verification! Check your app.')
        setLogs(prev => [...prev, { ts, level: 'error', msg: data.message }])
      } else if (eventType === 'log') {
        setLogs(prev => [...prev, { ts, level: data.level || 'info', msg: data.message }])
      }
    })
    sourceRef.current = source

    return () => source.close()
  }, [task.id])

  const togglePause = async () => {
    const newStatus = task.status === 'paused' ? 'running' : 'paused'
    await api.updateTask(task.id, newStatus)
    setTask(prev => ({ ...prev, status: newStatus }))
  }

  const copyLogs = async () => {
    const text = logs.map(l => `[${l.ts}] [${l.level}] ${l.msg}`).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback for non-secure contexts
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const progress = task.total_items > 0
    ? Math.round(((task.deleted + task.failed) / task.total_items) * 100)
    : 0

  if (compact) {
    return (
      <div className="flex items-center justify-between p-3 bg-gray-900/50 rounded-lg border border-gray-800/50">
        <div className="flex items-center gap-3 text-sm">
          <span className={`text-xs font-bold uppercase px-2 py-0.5 rounded ${
            task.platform === 'instagram' ? 'bg-purple-900/50 text-purple-400' : 'bg-blue-900/50 text-blue-400'
          }`}>
            {task.platform}
          </span>
          <span className="text-gray-400">{task.target_type}</span>
          <span className="text-gray-500">
            {task.deleted} deleted / {task.total_items} total
          </span>
          <span className={`text-xs px-2 py-0.5 rounded ${STATUS_COLORS[task.status]}`}>
            {task.status}
          </span>
        </div>
        <button
          onClick={() => onDelete(task.id)}
          className="text-xs text-gray-500 hover:text-red-400 transition"
        >
          remove
        </button>
      </div>
    )
  }

  const LOG_COLORS = {
    info: 'text-gray-400',
    ok: 'text-green-400',
    warn: 'text-yellow-400',
    error: 'text-red-400',
  }

  return (
    <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className={`text-xs font-bold uppercase px-2 py-1 rounded ${
            task.platform === 'instagram' ? 'bg-purple-900 text-purple-300' : 'bg-blue-900 text-blue-300'
          }`}>
            {task.platform}
          </span>
          <span className="font-medium">
            {task.target_type === 'likes' ? 'Removing likes' : 'Deleting comments'}
          </span>
          <span className={`text-xs px-2 py-0.5 rounded ${STATUS_COLORS[task.status]}`}>
            {task.status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {!['completed', 'failed'].includes(task.status) && (
            <button
              onClick={togglePause}
              className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 rounded-lg transition"
            >
              {task.status === 'paused' ? 'Resume' : 'Pause'}
            </button>
          )}
          <button
            onClick={() => setShowLogs(v => !v)}
            className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 rounded-lg transition"
          >
            {showLogs ? 'Hide Logs' : 'Show Logs'}
          </button>
          <button
            onClick={() => onDelete(task.id)}
            className="px-3 py-1.5 text-xs text-red-400 hover:bg-red-900/50 rounded-lg transition"
          >
            Delete
          </button>
        </div>
      </div>

      {/* Progress bar */}
      <div className="w-full bg-gray-800 rounded-full h-2 mb-2">
        <div
          className="bg-blue-500 h-2 rounded-full transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="flex justify-between text-xs text-gray-400">
        <span>{task.deleted} deleted{task.failed > 0 ? ` / ${task.failed} failed` : ''}</span>
        <span>
          {task.status === 'scanning'
            ? `Found ${task.total_items} items...`
            : `${task.total_items} total`
          }
        </span>
        <span>{progress}%</span>
      </div>

      {notice && (
        <div className="mt-2 p-2 text-xs bg-yellow-900/50 border border-yellow-700 rounded text-yellow-300">
          {notice}
        </div>
      )}

      {/* Live log panel */}
      {showLogs && (
        <div className="mt-3 bg-gray-950 rounded-lg border border-gray-800 overflow-hidden">
          <div className="px-3 py-1.5 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-500 font-medium">Live Logs</span>
              <span className="text-xs text-gray-600">{logs.length} entries</span>
              {autoCloseIn !== null && (
                <span className="text-xs text-yellow-500">logs stay open {autoCloseIn}s</span>
              )}
            </div>
            <button
              onClick={copyLogs}
              className={`px-2 py-0.5 text-xs rounded transition ${
                copied
                  ? 'bg-green-900/50 text-green-400'
                  : 'bg-gray-700 hover:bg-gray-600 text-gray-300'
              }`}
            >
              {copied ? 'Copied!' : 'Copy Logs'}
            </button>
          </div>
          <div className="p-2 max-h-80 overflow-y-auto font-mono text-xs leading-relaxed">
            {logs.length === 0 ? (
              <div className="text-gray-600 p-2">Waiting for events...</div>
            ) : (
              logs.map((log, i) => (
                <div key={i} className={`${LOG_COLORS[log.level] || 'text-gray-400'} whitespace-pre-wrap break-all`}>
                  <span className="text-gray-600">[{log.ts}]</span> {log.msg}
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </div>
      )}
    </div>
  )
}
