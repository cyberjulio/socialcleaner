import { useState, useEffect, useRef } from 'react'
import { api } from '../api.js'

const PLATFORMS = {
  instagram: {
    name: 'Instagram',
    color: 'from-purple-600 to-pink-500',
    domain: 'instagram.com',
    cookies: [
      { key: 'sessionid', label: 'sessionid', hint: 'Long numeric string' },
      { key: 'csrftoken', label: 'csrftoken', hint: '32-character token' },
      { key: 'ds_user_id', label: 'ds_user_id', hint: 'Your numeric user ID' },
    ],
  },
  twitter: {
    name: 'X (Twitter)',
    color: 'from-blue-600 to-cyan-500',
    domain: 'x.com',
    cookies: [
      { key: 'auth_token', label: 'auth_token', hint: '40-character hex string' },
      { key: 'ct0', label: 'ct0', hint: 'Long alphanumeric CSRF token' },
    ],
  },
}

function getConsoleSnippet(platform) {
  if (platform === 'instagram') {
    return `(async()=>{
  const needed=['sessionid','csrftoken','ds_user_id'];
  const found={};
  if(window.cookieStore){
    const all=await cookieStore.getAll();
    all.forEach(c=>{if(needed.includes(c.name))found[c.name]=c.value;});
  }
  document.cookie.split(';').forEach(c=>{
    const [k,...v]=c.trim().split('=');
    if(needed.includes(k)&&!found[k])found[k]=v.join('=');
  });
  const missing=needed.filter(n=>!found[n]);
  for(const name of missing){
    const val=prompt(name+' not found automatically.\\nCopy it from DevTools > Storage > Cookies and paste here:');
    if(val)found[name]=val.trim();
  }
  const still=needed.filter(n=>!found[n]);
  if(still.length){alert('Still missing: '+still.join(', '));return;}
  const data=btoa(JSON.stringify({platform:'instagram',cookies:found}));
  prompt('Copy this value, go back to socialcleaner, and paste it:',data);
})();`
  } else {
    return `(async()=>{
  const needed=['auth_token','ct0'];
  const found={};
  if(window.cookieStore){
    const all=await cookieStore.getAll();
    all.forEach(c=>{if(needed.includes(c.name))found[c.name]=c.value;});
  }
  document.cookie.split(';').forEach(c=>{
    const [k,...v]=c.trim().split('=');
    if(needed.includes(k)&&!found[k])found[k]=v.join('=');
  });
  const missing=needed.filter(n=>!found[n]);
  for(const name of missing){
    const val=prompt(name+' not found automatically.\\nCopy it from DevTools > Storage > Cookies and paste here:');
    if(val)found[name]=val.trim();
  }
  const still=needed.filter(n=>!found[n]);
  if(still.length){alert('Still missing: '+still.join(', '));return;}
  const data=btoa(JSON.stringify({platform:'twitter',cookies:found}));
  prompt('Copy this value, go back to socialcleaner, and paste it:',data);
})();`
  }
}

export default function CookieWizard({ onConnected, onError }) {
  const [platform, setPlatform] = useState(null)
  const [values, setValues] = useState({})
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [method, setMethod] = useState(null) // 'browser' | 'snippet' | 'manual'
  const [copied, setCopied] = useState(false)
  const [pasteValue, setPasteValue] = useState('')
  const [browserStatus, setBrowserStatus] = useState(null) // 'waiting' | 'success' | 'timeout' | 'error'
  const [browserError, setBrowserError] = useState(null)
  const pollRef = useRef(null)

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const config = platform ? PLATFORMS[platform] : null

  const handleBrowserLogin = async () => {
    setLoading(true)
    setBrowserStatus('waiting')
    setBrowserError(null)
    try {
      const { login_id } = await api.startBrowserLogin(platform)

      // Poll for status every 2 seconds
      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getBrowserLoginStatus(login_id)
          if (status.status === 'success') {
            clearInterval(pollRef.current)
            pollRef.current = null
            setBrowserStatus('success')
            setResult({ username: status.username })
            setLoading(false)
            setTimeout(() => onConnected(), 1500)
          } else if (status.status === 'timeout' || status.status === 'error') {
            clearInterval(pollRef.current)
            pollRef.current = null
            setBrowserStatus(status.status)
            setBrowserError(status.error)
            setLoading(false)
          }
        } catch {
          // Polling error — keep trying
        }
      }, 2000)
    } catch (err) {
      setBrowserStatus('error')
      setBrowserError(err.message)
      setLoading(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setResult(null)
    try {
      const res = await api.connect(platform, values)
      setResult(res)
      setTimeout(() => onConnected(), 1500)
    } catch (err) {
      onError(err.message)
    } finally {
      setLoading(false)
    }
  }

  // Platform selection screen
  if (!platform) {
    return (
      <div>
        <h2 className="text-lg font-semibold mb-6">Connect an account</h2>
        <div className="grid grid-cols-2 gap-4">
          {Object.entries(PLATFORMS).map(([key, p]) => (
            <button
              key={key}
              onClick={() => { setPlatform(key); setValues({}); setMethod(null); setResult(null); setBrowserStatus(null) }}
              className={`p-6 rounded-xl bg-gradient-to-br ${p.color} hover:opacity-90 transition text-white text-left`}
            >
              <div className="text-2xl font-bold">{p.name}</div>
              <div className="text-sm opacity-80 mt-1">Connect your account</div>
            </button>
          ))}
        </div>
      </div>
    )
  }

  // Method selection screen
  if (!method) {
    return (
      <div>
        <button
          onClick={() => { setPlatform(null); setResult(null) }}
          className="text-sm text-gray-400 hover:text-gray-200 mb-4"
        >
          &larr; Choose platform
        </button>

        <h2 className="text-lg font-semibold mb-4">
          Connect to {config.name}
        </h2>

        <div className="space-y-3">
          <button
            onClick={() => setMethod('browser')}
            className="w-full p-5 bg-gray-900 hover:bg-gray-800 border border-gray-700 rounded-xl text-left transition"
          >
            <div className="flex items-center gap-2">
              <span className="font-medium text-gray-100">Log in with browser</span>
              <span className="text-xs bg-green-900 text-green-300 px-2 py-0.5 rounded-full">recommended</span>
            </div>
            <div className="text-sm text-gray-400 mt-1">
              Opens a browser window where you log in normally. Cookies are captured automatically.
            </div>
          </button>

          <button
            onClick={() => setMethod('snippet')}
            className="w-full p-5 bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-xl text-left transition"
          >
            <div className="font-medium text-gray-100">Console snippet</div>
            <div className="text-sm text-gray-400 mt-1">
              Requires DevTools. Paste a snippet in the browser console on {config.domain} to extract cookies.
            </div>
          </button>

          <button
            onClick={() => setMethod('manual')}
            className="w-full p-5 bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-xl text-left transition"
          >
            <div className="font-medium text-gray-100">Manual cookie paste</div>
            <div className="text-sm text-gray-400 mt-1">
              Requires DevTools. Find cookies in the Storage tab on {config.domain} and paste them one by one.
            </div>
          </button>
        </div>
      </div>
    )
  }

  // Success state
  if (result) {
    return (
      <div>
        <div className="p-4 bg-green-900/50 border border-green-700 rounded-lg text-green-200">
          Connected as <span className="font-bold">@{result.username}</span>
        </div>
      </div>
    )
  }

  // Browser login method
  if (method === 'browser') {
    return (
      <div>
        <button
          onClick={() => {
            if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
            setMethod(null); setBrowserStatus(null); setBrowserError(null); setLoading(false)
          }}
          className="text-sm text-gray-400 hover:text-gray-200 mb-4"
        >
          &larr; Choose method
        </button>

        <h2 className="text-lg font-semibold mb-4">
          Connect to {config.name} — Browser Login
        </h2>

        {!browserStatus && (
          <div className="space-y-4">
            <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
              <p className="text-sm text-gray-300">
                A browser window will open with the {config.name} login page.
                Log in with your username and password as you normally would.
                The window will close automatically once you're logged in.
              </p>
            </div>
            <button
              onClick={handleBrowserLogin}
              disabled={loading}
              className="w-full py-3 rounded-lg font-medium transition bg-blue-600 hover:bg-blue-500 text-white"
            >
              Open Login Window
            </button>
          </div>
        )}

        {browserStatus === 'waiting' && (
          <div className="p-6 bg-gray-900 rounded-lg border border-gray-800 text-center">
            <div className="w-6 h-6 border-2 border-blue-400 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <p className="text-gray-200 font-medium mb-2">Browser window opened</p>
            <p className="text-sm text-gray-400">
              Log in to {config.name} in the browser window, then come back here.
              This page will update automatically.
            </p>
          </div>
        )}

        {browserStatus === 'timeout' && (
          <div className="space-y-4">
            <div className="p-4 bg-yellow-900/50 border border-yellow-700 rounded-lg text-yellow-200 text-sm">
              Login timed out after 5 minutes. The browser window has been closed.
            </div>
            <button
              onClick={() => { setBrowserStatus(null); setBrowserError(null) }}
              className="px-4 py-2 text-sm bg-gray-700 hover:bg-gray-600 rounded-lg transition"
            >
              Try again
            </button>
          </div>
        )}

        {browserStatus === 'error' && (
          <div className="space-y-4">
            <div className="p-4 bg-red-900/50 border border-red-700 rounded-lg text-red-200 text-sm">
              {browserError || 'An error occurred during login.'}
            </div>
            <button
              onClick={() => { setBrowserStatus(null); setBrowserError(null) }}
              className="px-4 py-2 text-sm bg-gray-700 hover:bg-gray-600 rounded-lg transition"
            >
              Try again
            </button>
          </div>
        )}
      </div>
    )
  }

  // Console snippet method
  if (method === 'snippet') {
    const snippet = getConsoleSnippet(platform)

    const handlePasteConnect = async () => {
      setLoading(true)
      try {
        const { platform: p, cookies } = JSON.parse(atob(pasteValue.trim()))
        const res = await api.connect(p, cookies)
        setResult(res)
        setTimeout(() => onConnected(), 1500)
      } catch (err) {
        onError(err.message || 'Invalid data — try copying the snippet output again')
      } finally {
        setLoading(false)
      }
    }

    return (
      <div>
        <button
          onClick={() => setMethod(null)}
          className="text-sm text-gray-400 hover:text-gray-200 mb-4"
        >
          &larr; Choose method
        </button>

        <h2 className="text-lg font-semibold mb-4">
          Connect to {config.name} — Console Snippet
        </h2>

        <div className="space-y-4">
          <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
            <p className="text-sm text-gray-300 mb-3">
              <span className="font-medium">Step 1:</span> Go to{' '}
              <span className="text-white font-mono">{config.domain}</span> in another tab
              (make sure you're logged in)
            </p>
          </div>

          <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
            <p className="text-sm text-gray-300 mb-3">
              <span className="font-medium">Step 2:</span> Open DevTools (F12 or Cmd+Option+I),
              go to the <span className="text-white">Console</span> tab, and paste this snippet:
            </p>
            <div className="relative">
              <pre className="p-3 bg-gray-950 rounded-lg text-xs text-gray-300 overflow-x-auto whitespace-pre-wrap break-all max-h-40 overflow-y-auto">
                {snippet}
              </pre>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(snippet)
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                }}
                className="absolute top-2 right-2 px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded transition"
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
          </div>

          <div className="p-4 bg-gray-900 rounded-lg border border-gray-800">
            <p className="text-sm text-gray-300 mb-3">
              <span className="font-medium">Step 3:</span> Come back here and paste the result:
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={pasteValue}
                onChange={(e) => setPasteValue(e.target.value)}
                placeholder="Paste the copied data here..."
                className="flex-1 px-3 py-2 bg-gray-950 border border-gray-700 rounded-lg text-gray-100
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                           font-mono text-sm"
              />
              <button
                onClick={handlePasteConnect}
                disabled={!pasteValue.trim() || loading}
                className={`px-5 py-2 rounded-lg font-medium text-sm transition
                  ${!pasteValue.trim() || loading
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-blue-600 hover:bg-blue-500 text-white'
                  }`}
              >
                {loading ? 'Connecting...' : 'Connect'}
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // Manual method
  return (
    <div>
      <button
        onClick={() => setMethod(null)}
        className="text-sm text-gray-400 hover:text-gray-200 mb-4"
      >
        &larr; Choose method
      </button>

      <h2 className="text-lg font-semibold mb-4">
        Connect to {config.name} — Manual
      </h2>

      <div className="mb-6 p-4 bg-gray-900 rounded-lg border border-gray-800">
        <h3 className="text-sm font-medium text-gray-300 mb-2">
          How to get your cookies:
        </h3>
        <ol className="text-sm text-gray-400 space-y-1 list-decimal list-inside">
          <li>Open {config.domain} in your browser and log in</li>
          <li>Press F12 (or Cmd+Option+I on Mac) to open DevTools</li>
          <li>Go to the Storage tab</li>
          <li>Expand Cookies &gt; https://{config.domain === 'x.com' ? 'x.com' : 'www.' + config.domain}</li>
          <li>Find and copy the values for each cookie below</li>
        </ol>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {config.cookies.map((cookie) => (
          <div key={cookie.key}>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              {cookie.label}
              <span className="text-gray-500 font-normal ml-2">{cookie.hint}</span>
            </label>
            <input
              type="text"
              value={values[cookie.key] || ''}
              onChange={(e) => setValues({ ...values, [cookie.key]: e.target.value.trim() })}
              required
              className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-100
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                         font-mono text-sm"
              placeholder={cookie.label}
            />
          </div>
        ))}

        <button
          type="submit"
          disabled={loading}
          className={`w-full py-3 rounded-lg font-medium transition
            ${loading
              ? 'bg-gray-700 text-gray-400 cursor-wait'
              : 'bg-blue-600 hover:bg-blue-500 text-white'
            }`}
        >
          {loading ? 'Validating...' : 'Connect'}
        </button>
      </form>
    </div>
  )
}
