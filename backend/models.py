from pydantic import BaseModel
from typing import Optional


class SessionCreate(BaseModel):
    platform: str  # 'instagram' | 'twitter'
    cookies: dict[str, str]


class SessionResponse(BaseModel):
    id: str
    platform: str
    username: Optional[str] = None
    valid: bool


class BrowserLoginRequest(BaseModel):
    platform: str  # 'instagram' | 'twitter'


class BrowserLoginStatus(BaseModel):
    login_id: str
    status: str  # 'waiting' | 'success' | 'timeout' | 'error'
    username: Optional[str] = None
    session_id: Optional[str] = None
    error: Optional[str] = None


class TaskCreate(BaseModel):
    session_id: str
    target_type: str  # 'likes' | 'comments'


class TaskResponse(BaseModel):
    id: str
    session_id: str
    platform: str
    target_type: str
    status: str
    total_items: int
    deleted: int
    failed: int
    created_at: str


class TaskUpdate(BaseModel):
    status: str  # 'paused' | 'running'
