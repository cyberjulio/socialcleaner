#!/bin/bash
# Start both backend and frontend in dev mode
set -e

cd "$(dirname "$0")/.."

# Activate virtual environment
if [ -f venv/bin/activate ]; then
  source venv/bin/activate
elif [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
else
  echo "Error: No Python virtual environment found."
  echo "Run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# Check if frontend dependencies are installed
if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

# Kill any previous instances by port
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :5173 | xargs kill -9 2>/dev/null || true
sleep 1

echo "Starting backend..."
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Starting frontend..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "Shutting down..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

echo ""
echo "  Backend:  http://127.0.0.1:8000"
echo "  Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop both servers."
echo ""

wait
