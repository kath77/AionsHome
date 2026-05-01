from __future__ import annotations
"""
摄像头 API + 监控日志 API
"""

import time, asyncio

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional

from config import SCREENSHOTS_DIR, MONITOR_LOGS_DIR, save_cam_config
from camera import cam, detect_cameras, read_monitor_logs

router = APIRouter()

# ── 摄像头控制 ────────────────────────────────────
class CamConfigUpdate(BaseModel):
    camera_index: Optional[int] = None
    auto_interval_min: Optional[int] = None
    auto_interval_max: Optional[int] = None
    max_screenshots: Optional[int] = None
    quiet_hours_enabled: Optional[bool] = None
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None

@router.get("/api/cam/cameras")
async def list_cameras():
    # 跳过当前正在使用的摄像头，避免 DirectShow 设备冲突导致采集线程中断
    skip = cam.cfg["camera_index"] if cam.running else -1
    cams = await asyncio.get_event_loop().run_in_executor(None, lambda: detect_cameras(skip_index=skip))
    return {"cameras": cams, "current": cam.cfg["camera_index"]}

@router.get("/api/cam/status")
async def cam_status():
    remaining = 0
    if cam.monitoring and cam._next_capture_at > 0:
        remaining = max(0, cam._next_capture_at - time.time())
    return {
        "camera_open": cam.running,
        "monitoring": cam.monitoring,
        "camera_index": cam.cfg["camera_index"],
        "auto_interval_min": cam.cfg.get("auto_interval_min", 10),
        "auto_interval_max": cam.cfg.get("auto_interval_max", 20),
        "max_screenshots": cam.cfg["max_screenshots"],
        "quiet_hours_enabled": cam.cfg.get("quiet_hours_enabled", False),
        "quiet_hours_start": cam.cfg.get("quiet_hours_start", "00:00"),
        "quiet_hours_end": cam.cfg.get("quiet_hours_end", "09:00"),
        "is_quiet_hours": cam._is_quiet_hours(),
        "next_capture_in": round(remaining),
    }

@router.post("/api/cam/open")
async def cam_open(camera_index: int = 0):
    if cam._cam_op_lock.locked():
        return {"ok": False, "message": "摄像头操作进行中，请稍候"}
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, lambda: cam.open_camera(camera_index))
    return {"ok": ok, "camera_index": cam.cfg["camera_index"],
            "message": "摄像头已打开" if ok else "无法打开摄像头，请检查连接"}

@router.post("/api/cam/close")
async def cam_close():
    if cam._cam_op_lock.locked():
        return {"ok": False, "message": "摄像头操作进行中，请稍候"}
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, cam.close_camera)
    return {"ok": True}

@router.post("/api/cam/monitor/start")
async def cam_monitor_start():
    # start_monitoring 可能调用 open_camera（阻塞），需要在线程中执行
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, cam.start_monitoring)
    return {"ok": True, "monitoring": True}

@router.post("/api/cam/monitor/stop")
async def cam_monitor_stop():
    cam.stop_monitoring()
    return {"ok": True, "monitoring": False}

@router.post("/api/cam/screenshot")
async def cam_screenshot():
    filename = cam.save_screenshot()
    if not filename:
        return {"error": "无画面"}
    return {"ok": True, "filename": filename}

@router.put("/api/cam/config")
async def update_cam_config(body: CamConfigUpdate):
    if body.camera_index is not None:
        cam.cfg["camera_index"] = body.camera_index
    if body.auto_interval_min is not None:
        cam.cfg["auto_interval_min"] = max(1, body.auto_interval_min)
    if body.auto_interval_max is not None:
        cam.cfg["auto_interval_max"] = max(cam.cfg.get("auto_interval_min", 1), body.auto_interval_max)
    if body.max_screenshots is not None:
        cam.cfg["max_screenshots"] = max(0, body.max_screenshots)
    if body.quiet_hours_enabled is not None:
        cam.cfg["quiet_hours_enabled"] = body.quiet_hours_enabled
    if body.quiet_hours_start is not None:
        cam.cfg["quiet_hours_start"] = body.quiet_hours_start
    if body.quiet_hours_end is not None:
        cam.cfg["quiet_hours_end"] = body.quiet_hours_end
    save_cam_config(cam.cfg)
    return {"ok": True}

@router.get("/api/cam/frame")
async def cam_frame():
    jpg = cam.get_frame_jpeg()
    if not jpg:
        return Response(content=b'', status_code=204)
    return Response(content=jpg, media_type="image/jpeg")

# ── 监控日志 ──────────────────────────────────────

# ── 画面裁剪（缩放/平移）──────────────────────────
class CropUpdate(BaseModel):
    zoom: float = 1.0
    cx: float = 0.5
    cy: float = 0.5

@router.get("/api/cam/crop")
async def get_crop():
    return cam.get_crop()

@router.put("/api/cam/crop")
async def set_crop(body: CropUpdate):
    cam.set_crop(body.zoom, body.cx, body.cy)
    return cam.get_crop()

# ── 监控日志 ──────────────────────────────────────
@router.get("/api/cam/logs")
async def list_log_dates():
    dates = []
    for f in sorted(MONITOR_LOGS_DIR.glob("*.jsonl"), reverse=True):
        dates.append(f.stem)
    return {"dates": dates}

@router.get("/api/cam/logs/{date_str}")
async def get_log_entries(date_str: str):
    entries = read_monitor_logs(date_str)
    return {"date": date_str, "entries": entries}

@router.get("/api/cam/logs/today/entries")
async def get_today_logs():
    entries = read_monitor_logs()
    return {"date": time.strftime('%Y-%m-%d'), "entries": entries}
