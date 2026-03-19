# SocialCleaner

A self-hosted tool for bulk-removing your likes and comments from Instagram
(and eventually Twitter/X). It runs entirely on your machine -- your
credentials never leave your computer.

SocialCleaner uses browser automation (Playwright) to interact with
Instagram's web interface the same way you would manually, just faster.

## How it works

SocialCleaner can be used via the **CLI** (terminal menu) or the **Web Dashboard**.

1. You connect your Instagram account — either by logging in through a
   browser window (CLI) or by providing session cookies (Web).
2. SocialCleaner opens a headless browser with your session and navigates to
   **Your Activity > Likes** or **Your Activity > Comments**.
3. It selects items in small batches, clicks Unlike/Delete, confirms, and
   verifies each removal before moving on.
4. Progress is shown live — in a terminal progress bar (CLI) or streamed
   to the web dashboard via server-sent events.

Your cookies are encrypted at rest with Fernet (AES-128-CBC) and stored in a
local SQLite database. Nothing is sent to any external server. Both the CLI
and web dashboard share the same database — accounts and tasks are visible
in either interface.

## Requirements

- Python 3.10 or later
- Node.js 18 or later
- A modern browser (Firefox recommended) with an active Instagram session

## Installation

### macOS

```bash
# Clone the repository
git clone https://github.com/cyberjulio/socialcleaner.git
cd socialcleaner

# Create and activate a Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers (Firefox is used for automation)
playwright install firefox

# Install frontend dependencies
cd frontend
npm install
cd ..

# Create your environment file
cp .env.example .env
# Edit .env and replace the secret key with a random string:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Linux (Debian/Ubuntu)

```bash
# Install system dependencies for Playwright
sudo apt-get update
sudo apt-get install -y python3 python3-venv nodejs npm

# Clone the repository
git clone https://github.com/cyberjulio/socialcleaner.git
cd socialcleaner

# Create and activate a Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers and their system dependencies
playwright install firefox
playwright install-deps firefox

# Install frontend dependencies
cd frontend
npm install
cd ..

# Create your environment file
cp .env.example .env
# Edit .env and replace the secret key with a random string:
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Running

### Option A: CLI (recommended)

The CLI provides a menu-driven interface with no browser needed for setup:

```bash
source venv/bin/activate
python -m cli
```

This launches an interactive menu:

```
  1. Start Web Dashboard
  2. Unlike Instagram Posts
  3. Delete Instagram Comments
  4. Manage Accounts
  5. About
  6. Quit
```

Press a number to navigate — no Enter key needed.

### Option B: Web Dashboard

You can also launch the web dashboard from the CLI (option 1), or start it
directly:

```bash
source venv/bin/activate
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 in your browser.

### Development mode

For development (with hot reload on both servers):

```bash
./scripts/dev.sh
```

## Connecting your Instagram account

### Via CLI (recommended)

1. Launch the CLI: `python -m cli`
2. Press **4** (Manage Accounts) then **1** (Add Instagram Account)
3. A Firefox browser window opens to the Instagram login page
4. Log in normally — handle 2FA if prompted
5. The CLI detects when you're logged in and saves your session

### Via Web Dashboard

1. Click **+ Connect Account** on the dashboard.
2. Choose **Instagram**.
3. Follow the instructions to extract your session cookies. Two methods
   are provided:
   - **Bookmarklet** (recommended): drag the bookmarklet link to your
     bookmarks bar, navigate to instagram.com while logged in, and click it.
   - **Manual**: open DevTools > Application > Cookies on instagram.com and
     copy the required values (`sessionid`, `csrftoken`, `ds_user_id`,
     `ig_did`, `mid`).
4. Once connected, your account appears on the dashboard with action buttons.

Accounts are shared between CLI and web — add once, use in either.

## Usage

### CLI

- **Unlike Instagram Posts** (option 2) — removes all your likes
- **Delete Instagram Comments** (option 3) — removes all your comments
- Live progress bar shows count, speed, and estimated time
- Press **Q** to stop (progress is saved), **P** to pause/resume
- If you stop mid-task, the CLI will offer to resume next time

### Web Dashboard

- **Unlike All** -- removes all your likes, newest first.
- **Delete Comments** -- removes all your comments.
- **Cancel** -- stops a running task. Progress is saved and you can see
  historical tasks with their logs.

Tasks run in the background. You can close the browser tab and come back
later; the backend continues processing.

## Project structure

```
socialcleaner/
  cli/
    __main__.py        CLI entry point (python -m cli)
    app.py             Main menu loop and signal handling
    auth.py            Interactive browser login and account management
    tasks.py           Task execution with rich progress display
    display.py         Shared rich TUI components
  backend/
    main.py            FastAPI application entry point
    config.py          Settings (secret key, DB path)
    database.py        SQLite schema and connection
    platforms/
      instagram.py     Instagram automation (likes, comments)
      twitter.py       Twitter/X automation (stub)
    routers/
      auth.py          Session management endpoints
      tasks.py         Task CRUD and SSE streaming
    utils/
      crypto.py        Fernet encryption for cookie storage
      events.py        Server-sent event bus
    worker/
      engine.py        Task execution engine (with TaskEventSink protocol)
      rate_limiter.py  Adaptive rate limiting
  frontend/
    src/
      App.jsx          Main application shell
      api.js           Backend API client
      components/
        Dashboard.jsx  Connected accounts and task list
        TaskCard.jsx   Task progress display with live logs
        CookieWizard.jsx  Account connection flow
```

## Important notes

- This tool automates actions on your own account. Use it responsibly.
- When you connect an account, SocialCleaner captures the user-agent
  string from the browser you use to access the dashboard. The headless
  browser that performs automation then uses that exact same user-agent.
  This means the requests look identical to your normal browsing session,
  reducing the chance of detection by the platform.
  For best results, connect your account using the same browser where you
  are logged into Instagram (e.g. if you use Firefox for Instagram, open
  the SocialCleaner dashboard in Firefox too).
- Instagram may temporarily restrict your account if you remove content
  too quickly. SocialCleaner includes rate limiting and automatic pauses,
  but there is always some risk.
- Session cookies expire. If you see "No accounts connected" after a
  restart, reconnect your account.
- The database and browser data are stored locally. Back up `cleaner.db`
  if you want to preserve your task history.

## Rate limiting and anti-detection

SocialCleaner is designed to run unattended for days, processing large
volumes of likes and comments without triggering Instagram's automation
detection. The rate limiting strategy is based on community research
across automation forums, GitHub projects, and real-world experience.

### How it works

Tasks process items in batches of 20-25 (matching Instagram's own
native UI cap of 25 selections). Between each batch, the tool waits a
random 20-45 seconds before starting the next one. Individual clicks
within a batch are spaced 200-600ms apart with randomization to avoid
fixed-timing detection.

### Session and daily limits

| Parameter              | Value            | Purpose                                      |
|------------------------|------------------|----------------------------------------------|
| Batch size             | 20-25 (random)   | Matches Instagram's native selection cap      |
| Click delay            | 200-600ms        | Randomized per click to mimic human input     |
| Inter-batch delay      | 20-45 seconds    | Community-recommended safe window             |
| Reading pause          | 20% chance, 3-8s | Simulates human scanning between actions      |
| Session active limit   | 50 minutes       | Max continuous operation before mandatory rest |
| Session rest           | 30-45 minutes    | Break between active sessions                 |
| Daily cap              | 800 actions      | Under the moderate-risk threshold of 1,000    |

### Action block detection

After each batch, SocialCleaner checks the page for Instagram block
indicators ("Try Again Later", "Action Blocked", "We restrict certain
activity"). If detected, all activity pauses for 24 hours to prevent
escalation from a temporary soft block to a longer restriction.

### Estimated duration

When a task starts, SocialCleaner calculates an estimated duration based
on the number of items, batch processing time, inter-batch delays,
session rests, and the daily cap. For example:

- 80 items: approximately 28 minutes
- 800 items: approximately 1 day
- 5,000 items: approximately 7 days (processing 800 items/day)

### Instagram rate limit reference

These numbers are community-derived (Instagram does not publish official
limits) and represent conservative safe thresholds:

| Action type     | Safe per hour | Safe per day | Risk threshold |
|-----------------|---------------|--------------|----------------|
| Likes / Unlikes | 30-50         | 500-1,000    | >1,000/day     |
| Comments        | 20            | 150-200      | >200/day       |
| Follows         | 30            | 500          | >500/day       |
| Combined total  | --            | 300-800      | >1,000/day     |

Action blocks escalate with repeated violations:

1. Soft block (single action type, few hours to 24h)
2. Temporary block (24-48h, shows expiration)
3. Extended block (days to 2 weeks, no expiration shown)
4. Full restriction (up to 30 days)
5. Suspension (180 days or permanent)

Continuing automated actions during a block extends its duration.
SocialCleaner detects blocks and stops automatically to avoid this.

## License

This project is licensed under [CC BY-NC 4.0](LICENSE). You are free to
use, modify, and share it for non-commercial purposes. Commercial use is
not permitted.
