# SocialCleaner

A self-hosted tool for bulk-removing your likes and comments from Instagram
(and eventually Twitter/X). It runs entirely on your machine -- your
credentials never leave your computer.

SocialCleaner uses browser automation (Playwright) to interact with
Instagram's web interface the same way you would manually, just faster.

## How it works

1. You connect your Instagram account by providing session cookies from
   your browser (via a bookmarklet or manual paste).
2. SocialCleaner opens a headless browser with your session and navigates to
   **Your Activity > Likes** or **Your Activity > Comments**.
3. It selects items in small batches, clicks Unlike/Delete, confirms, and
   verifies each removal before moving on.
4. Progress is streamed to the dashboard in real time via server-sent events.

Your cookies are encrypted at rest with Fernet (AES-128-CBC) and stored in a
local SQLite database. Nothing is sent to any external server.

## Requirements

- Python 3.10 or later
- Node.js 18 or later
- A modern browser (Firefox recommended) with an active Instagram session

## Installation

### macOS

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/socialcleaner.git
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
git clone https://github.com/YOUR_USERNAME/socialcleaner.git
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

Start the backend and frontend in two separate terminals:

**Terminal 1 -- Backend:**

```bash
source venv/bin/activate
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 -- Frontend:**

```bash
cd frontend
npm run dev
```

Open http://127.0.0.1:5173 in your browser.

## Connecting your Instagram account

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

## Usage

- **Unlike All** -- removes all your likes, newest first.
- **Delete Comments** -- removes all your comments.
- **Cancel** -- stops a running task. Progress is saved and you can see
  historical tasks with their logs.

Tasks run in the background. You can close the browser tab and come back
later; the backend continues processing.

## Project structure

```
socialcleaner/
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
      engine.py        Task execution engine
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
- Instagram may temporarily restrict your account if you remove content
  too quickly. SocialCleaner includes rate limiting and automatic pauses,
  but there is always some risk.
- Session cookies expire. If you see "No accounts connected" after a
  restart, reconnect your account.
- The database and browser data are stored locally. Back up `cleaner.db`
  if you want to preserve your task history.

## License

This project is licensed under [CC BY-NC 4.0](LICENSE). You are free to
use, modify, and share it for non-commercial purposes. Commercial use is
not permitted.
