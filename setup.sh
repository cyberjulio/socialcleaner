#!/bin/bash
# SocialCleaner — one-step setup
set -e

cd "$(dirname "$0")"

echo "Setting up SocialCleaner..."
echo ""

# Python virtual environment
if [ ! -d venv ]; then
  echo "[1/5] Creating Python virtual environment..."
  python3 -m venv venv
else
  echo "[1/5] Python virtual environment already exists."
fi

source venv/bin/activate

# Python dependencies
echo "[2/5] Installing Python dependencies..."
pip install -q -r requirements.txt

# Playwright browser
echo "[3/5] Installing Playwright Firefox..."
playwright install firefox
# Install system deps on Linux (no-op on macOS)
if [ "$(uname)" = "Linux" ]; then
  playwright install-deps firefox
fi

# Environment file
if [ ! -f .env ]; then
  echo "[4/5] Generating secret key and creating .env..."
  python3 -c "import secrets; print('CLEANER_SECRET_KEY=' + secrets.token_urlsafe(32))" > .env
else
  echo "[4/5] .env already exists, skipping."
fi

# Frontend build
echo "[5/5] Building web dashboard..."
(cd frontend && npm install --silent && npm run build --silent)

echo ""
echo "Setup complete. Run with:"
echo ""
echo "  source venv/bin/activate"
echo "  python -m cli"
echo ""
