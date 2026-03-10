import { useState, useEffect } from 'react'
import { api } from './api.js'
import CookieWizard from './components/CookieWizard.jsx'
import Dashboard from './components/Dashboard.jsx'

export default function App() {
  const [sessions, setSessions] = useState([])
  const [tasks, setTasks] = useState([])
  const [view, setView] = useState('dashboard') // 'dashboard' | 'connect'
  const [error, setError] = useState(null)
  const [importStatus, setImportStatus] = useState(null) // 'loading' | 'success' | 'error'
  const [importResult, setImportResult] = useState(null)

  const refresh = async (retries = 3) => {
    for (let i = 0; i < retries; i++) {
      try {
        const [s, t] = await Promise.all([api.getSessions(), api.getTasks()])
        setSessions(s)
        setTasks(t)
        return
      } catch (e) {
        if (i === retries - 1) {
          setError(e.message)
        } else {
          await new Promise(r => setTimeout(r, 2000))
        }
      }
    }
  }

  useEffect(() => { refresh() }, [])

  // Handle bookmarklet redirect: #import/<base64 data>
  useEffect(() => {
    const hash = window.location.hash
    if (!hash.startsWith('#import/')) return

    const encoded = hash.slice('#import/'.length)
    window.location.hash = ''

    try {
      const { platform, cookies } = JSON.parse(atob(encoded))
      setImportStatus('loading')
      api.connect(platform, cookies)
        .then((res) => {
          setImportStatus('success')
          setImportResult(res)
          refresh()
          setTimeout(() => { setImportStatus(null); setImportResult(null) }, 4000)
        })
        .catch((err) => {
          setImportStatus('error')
          setError(err.message)
          setTimeout(() => setImportStatus(null), 4000)
        })
    } catch {
      setError('Invalid bookmarklet data')
    }
  }, [])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <h1
            className="text-xl font-bold tracking-tight cursor-pointer"
            onClick={() => setView('dashboard')}
          >
            <span className="bg-black text-white px-1.5 py-0.5 rounded-l">social</span><span className="bg-white text-black px-1.5 py-0.5 rounded-r">cleaner</span>
          </h1>
          <button
            onClick={() => setView(view === 'connect' ? 'dashboard' : 'connect')}
            className="px-4 py-2 text-sm bg-gray-800 hover:bg-gray-700 rounded-lg transition"
          >
            {view === 'connect' ? 'Back to Dashboard' : '+ Connect Account'}
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        {importStatus === 'loading' && (
          <div className="mb-4 p-3 bg-blue-900/50 border border-blue-700 rounded-lg text-blue-200 text-sm flex items-center gap-2">
            <div className="w-2 h-2 bg-blue-400 rounded-full animate-pulse" />
            Connecting account from bookmarklet...
          </div>
        )}
        {importStatus === 'success' && importResult && (
          <div className="mb-4 p-3 bg-green-900/50 border border-green-700 rounded-lg text-green-200 text-sm">
            Connected as <span className="font-bold">@{importResult.username}</span>
          </div>
        )}
        {error && (
          <div className="mb-4 p-3 bg-red-900/50 border border-red-700 rounded-lg text-red-200 text-sm">
            {error}
            <button onClick={() => setError(null)} className="ml-2 underline">dismiss</button>
          </div>
        )}

        {view === 'connect' ? (
          <CookieWizard
            onConnected={() => { refresh(); setView('dashboard') }}
            onError={setError}
          />
        ) : (
          <Dashboard
            sessions={sessions}
            tasks={tasks}
            onRefresh={refresh}
            onError={setError}
          />
        )}
      </main>
    </div>
  )
}
