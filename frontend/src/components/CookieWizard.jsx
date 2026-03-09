import { useState } from 'react'
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
    instructions: [
      'Open instagram.com in your browser and log in',
      'Press F12 (or Cmd+Option+I on Mac) to open Developer Tools',
      'Go to the Storage tab',
      'In the left sidebar, expand Cookies > https://www.instagram.com (under Storage)',
      'Find and copy the values for each cookie below',
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
    instructions: [
      'Open x.com in your browser and log in',
      'Press F12 (or Cmd+Option+I on Mac) to open Developer Tools',
      'Go to the Storage tab',
      'In the left sidebar, expand Cookies > https://x.com (under Storage)',
      'Find and copy the values for each cookie below',
    ],
  },
}

function getConsoleSnippet(platform) {
  // Snippet the user pastes into the browser console on instagram.com / x.com
  // Reads all cookies (including httpOnly via the Cookie Store API or document.cookie fallback),
  // prompts for any missing httpOnly ones, and copies the result to clipboard
  if (platform === 'instagram') {
    return `(async()=>{
  const needed=['sessionid','csrftoken','ds_user_id'];
  const found={};
  // Try Cookie Store API first (can read httpOnly in some browsers)
  if(window.cookieStore){
    const all=await cookieStore.getAll();
    all.forEach(c=>{if(needed.includes(c.name))found[c.name]=c.value;});
  }
  // Fallback to document.cookie for non-httpOnly
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
  prompt('Copy this value, go back to cleaner, and paste it:',data);
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
  prompt('Copy this value, go back to cleaner, and paste it:',data);
})();`
  }
}

export default function CookieWizard({ onConnected, onError }) {
  const [platform, setPlatform] = useState(null)
  const [values, setValues] = useState({})
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [method, setMethod] = useState(null) // 'auto' | 'manual'
  const [copied, setCopied] = useState(false)
  const [pasteValue, setPasteValue] = useState('')

  const config = platform ? PLATFORMS[platform] : null

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
              onClick={() => { setPlatform(key); setValues({}); setMethod(null); setResult(null) }}
              className={`p-6 rounded-xl bg-gradient-to-br ${p.color} hover:opacity-90 transition text-white text-left`}
            >
              <div className="text-2xl font-bold">{p.name}</div>
              <div className="text-sm opacity-80 mt-1">
                {p.cookies.length} cookies needed
              </div>
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
            onClick={() => setMethod('auto')}
            className="w-full p-5 bg-gray-900 hover:bg-gray-800 border border-gray-700 rounded-xl text-left transition"
          >
            <div className="font-medium text-gray-100">Quick connect (recommended)</div>
            <div className="text-sm text-gray-400 mt-1">
              Run a snippet in the browser console on {config.domain} — auto-extracts cookies and copies them for you
            </div>
          </button>

          <button
            onClick={() => setMethod('manual')}
            className="w-full p-5 bg-gray-900 hover:bg-gray-800 border border-gray-800 rounded-xl text-left transition"
          >
            <div className="font-medium text-gray-100">Manual paste</div>
            <div className="text-sm text-gray-400 mt-1">
              Open DevTools, find cookies in the Storage tab, and paste them one by one
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

  // Auto (console snippet) method
  if (method === 'auto') {
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
          Connect to {config.name} — Quick Connect
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
            <p className="text-xs text-gray-500 mt-2">
              The snippet reads your cookies, prompts for any it can't access, and copies the result to your clipboard.
            </p>
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
          {config.instructions.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
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
