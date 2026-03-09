import { useState } from 'react'
import { api } from '../api.js'
import TaskCard from './TaskCard.jsx'

export default function Dashboard({ sessions, tasks, onRefresh, onError }) {
  const [creating, setCreating] = useState(null) // session id being used

  const handleCreate = async (sessionId, targetType) => {
    try {
      await api.createTask(sessionId, targetType)
      onRefresh()
    } catch (e) {
      onError(e.message)
    }
  }

  const handleDelete = async (taskId) => {
    try {
      await api.deleteTask(taskId)
      onRefresh()
    } catch (e) {
      onError(e.message)
    }
  }

  const handleDeleteSession = async (sessionId) => {
    try {
      await api.deleteSession(sessionId)
      onRefresh()
    } catch (e) {
      onError(e.message)
    }
  }

  const activeTasks = tasks.filter(t => !['completed', 'failed'].includes(t.status))
  const completedTasks = tasks.filter(t => ['completed', 'failed'].includes(t.status))

  return (
    <div>
      {/* Connected accounts */}
      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-4">Connected Accounts</h2>
        {sessions.length === 0 ? (
          <p className="text-gray-500 text-sm">
            No accounts connected. Click "+ Connect Account" to get started.
          </p>
        ) : (
          <div className="space-y-3">
            {sessions.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between p-4 bg-gray-900 rounded-lg border border-gray-800"
              >
                <div className="flex items-center gap-3">
                  <span className={`text-xs font-bold uppercase px-2 py-1 rounded ${
                    s.platform === 'instagram'
                      ? 'bg-purple-900 text-purple-300'
                      : 'bg-blue-900 text-blue-300'
                  }`}>
                    {s.platform}
                  </span>
                  <span className="font-medium">@{s.username}</span>
                  {!s.valid && <span className="text-xs text-red-400">expired</span>}
                </div>
                <div className="flex items-center gap-2">
                  {creating === s.id ? (
                    <div className="flex gap-2">
                      <button
                        onClick={() => { handleCreate(s.id, 'likes'); setCreating(null) }}
                        className="px-3 py-1.5 text-xs bg-orange-700 hover:bg-orange-600 rounded-lg transition"
                      >
                        Unlike All
                      </button>
                      <button
                        onClick={() => { handleCreate(s.id, 'comments'); setCreating(null) }}
                        className="px-3 py-1.5 text-xs bg-red-700 hover:bg-red-600 rounded-lg transition"
                      >
                        Delete Comments
                      </button>
                      <button
                        onClick={() => setCreating(null)}
                        className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 rounded-lg transition"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        onClick={() => setCreating(s.id)}
                        className="px-3 py-1.5 text-xs bg-gray-700 hover:bg-gray-600 rounded-lg transition"
                      >
                        New Task
                      </button>
                      <button
                        onClick={() => handleDeleteSession(s.id)}
                        className="px-3 py-1.5 text-xs text-red-400 hover:bg-red-900/50 rounded-lg transition"
                      >
                        Disconnect
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Active tasks */}
      {activeTasks.length > 0 && (
        <section className="mb-8">
          <h2 className="text-lg font-semibold mb-4">Active Tasks</h2>
          <div className="space-y-3">
            {activeTasks.map((t) => (
              <TaskCard key={t.id} task={t} onRefresh={onRefresh} onDelete={handleDelete} />
            ))}
          </div>
        </section>
      )}

      {/* Completed tasks */}
      {completedTasks.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-gray-500 mb-3">History</h2>
          <div className="space-y-2">
            {completedTasks.map((t) => (
              <TaskCard key={t.id} task={t} onRefresh={onRefresh} onDelete={handleDelete} compact />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
