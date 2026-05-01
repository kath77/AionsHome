from __future__ import annotations
"""
日程路由：列表 / 手动添加 / 删除
"""

import time
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional

import aiosqlite
from database import get_db
from ws import manager

router = APIRouter()


class ScheduleCreate(BaseModel):
    type: str = "alarm"          # alarm / reminder
    trigger_at: str              # ISO: 2026-03-25T10:00
    content: str


@router.get("/api/schedules")
async def list_schedules(status: Optional[str] = Query(None)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM schedules WHERE status=? ORDER BY trigger_at", (status,)
            )
        else:
            cur = await db.execute("SELECT * FROM schedules ORDER BY trigger_at")
        return [dict(r) for r in await cur.fetchall()]


@router.post("/api/schedules")
async def create_schedule(body: ScheduleCreate):
    sid = f"sch_{int(time.time()*1000)}"
    now = time.time()
    trigger_at = body.trigger_at.replace("T", " ")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO schedules (id, type, trigger_at, content, created_at, status) VALUES (?,?,?,?,?,?)",
            (sid, body.type, trigger_at, body.content, now, "active"),
        )
        await db.commit()
    item = {"id": sid, "type": body.type, "trigger_at": trigger_at,
            "content": body.content, "created_at": now, "status": "active"}
    await manager.broadcast({"type": "schedule_changed"})
    return item


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str):
    async with get_db() as db:
        await db.execute("UPDATE schedules SET status='cancelled' WHERE id=?", (schedule_id,))
        await db.commit()
    await manager.broadcast({"type": "schedule_changed"})
    return {"ok": True}
