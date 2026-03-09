import json
import uuid
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.database import get_db
from backend.models import TaskCreate, TaskResponse, TaskUpdate
from backend.utils.events import event_bus
from backend.worker.engine import worker_engine

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse)
async def create_task(data: TaskCreate):
    if data.target_type not in ("likes", "comments"):
        raise HTTPException(400, "target_type must be 'likes' or 'comments'")

    db = await get_db()
    try:
        session = await db.execute_fetchall(
            "SELECT * FROM sessions WHERE id = ? AND valid = 1", (data.session_id,)
        )
        if not session:
            raise HTTPException(404, "Session not found or invalid")
        session = session[0]

        task_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO tasks (id, session_id, platform, target_type) VALUES (?, ?, ?, ?)",
            (task_id, data.session_id, session["platform"], data.target_type),
        )
        await db.commit()

        # Schedule the task
        worker_engine.schedule_task(task_id)

        task = await db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
        t = task[0]
        return TaskResponse(
            id=t["id"], session_id=t["session_id"], platform=t["platform"],
            target_type=t["target_type"], status=t["status"],
            total_items=t["total_items"], deleted=t["deleted"],
            failed=t["failed"], created_at=t["created_at"],
        )
    finally:
        await db.close()


@router.get("", response_model=list[TaskResponse])
async def list_tasks():
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM tasks ORDER BY created_at DESC")
        return [
            TaskResponse(
                id=r["id"], session_id=r["session_id"], platform=r["platform"],
                target_type=r["target_type"], status=r["status"],
                total_items=r["total_items"], deleted=r["deleted"],
                failed=r["failed"], created_at=r["created_at"],
            )
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            raise HTTPException(404, "Task not found")
        r = rows[0]
        return TaskResponse(
            id=r["id"], session_id=r["session_id"], platform=r["platform"],
            target_type=r["target_type"], status=r["status"],
            total_items=r["total_items"], deleted=r["deleted"],
            failed=r["failed"], created_at=r["created_at"],
        )
    finally:
        await db.close()


@router.patch("/{task_id}")
async def update_task(task_id: str, data: TaskUpdate):
    if data.status not in ("paused", "running"):
        raise HTTPException(400, "Can only set status to 'paused' or 'running'")

    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            raise HTTPException(404, "Task not found")

        await db.execute(
            "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (data.status, task_id),
        )
        await db.commit()

        if data.status == "running":
            worker_engine.schedule_task(task_id)

        return {"ok": True, "status": data.status}
    finally:
        await db.close()


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM items WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.get("/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE stream for real-time task progress."""
    queue = event_bus.subscribe(task_id)

    async def generate():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(task_id, queue)

    return StreamingResponse(generate(), media_type="text/event-stream")
