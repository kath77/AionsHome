from __future__ import annotations
"""
设置、世界书、模型列表、TTS 路由
"""

import json, time

from fastapi import APIRouter
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from typing import Optional

import httpx

from config import SETTINGS, MODELS, save_settings, get_key, load_worldbook, save_worldbook, load_chat_status, TTS_CACHE_DIR
from database import get_db

router = APIRouter()

# ── 模型列表 ──────────────────────────────────────
@router.get("/api/models")
async def list_models():
    return [{"key": k, "provider": v["provider"]} for k, v in MODELS.items()]

# ── 设置 ──────────────────────────────────────────
class SettingsUpdate(BaseModel):
    gemini_key: Optional[str] = None
    siliconflow_key: Optional[str] = None
    gemini_free_key: Optional[str] = None
    aipro_key: Optional[str] = None
    aipro_base_url: Optional[str] = None
    netease_music_u: Optional[str] = None
    proactive_enabled: Optional[bool] = None
    proactive_inactivity_enabled: Optional[bool] = None
    proactive_routine_enabled: Optional[bool] = None
    proactive_goal_enabled: Optional[bool] = None
    proactive_emotion_enabled: Optional[bool] = None
    proactive_location_enabled: Optional[bool] = None
    proactive_activity_enabled: Optional[bool] = None
    proactive_festival_enabled: Optional[bool] = None
    proactive_promise_enabled: Optional[bool] = None
    proactive_inactivity_hours: Optional[float] = None
    proactive_config: Optional[dict] = None
    proactive_quiet_enabled: Optional[bool] = None
    proactive_quiet_window: Optional[str] = None
    gift_prefer_html: Optional[bool] = None

@router.get("/api/settings")
async def get_settings():
    def mask(k):
        if not k or len(k) < 8:
            return k
        return k[:4] + "*" * (len(k) - 8) + k[-4:]
    return {
        "gemini_key": SETTINGS.get("gemini_key", ""),
        "siliconflow_key": SETTINGS.get("siliconflow_key", ""),
        "gemini_free_key": SETTINGS.get("gemini_free_key", ""),
        "aipro_key": SETTINGS.get("aipro_key", ""),
        "aipro_base_url": SETTINGS.get("aipro_base_url", "https://key.simpleai.com.cn/v1"),
        "netease_music_u": SETTINGS.get("netease_music_u", ""),
        "gemini_key_masked": mask(SETTINGS.get("gemini_key", "")),
        "siliconflow_key_masked": mask(SETTINGS.get("siliconflow_key", "")),
        "gemini_free_key_masked": mask(SETTINGS.get("gemini_free_key", "")),
        "aipro_key_masked": mask(SETTINGS.get("aipro_key", "")),
        "netease_music_u_masked": mask(SETTINGS.get("netease_music_u", "")),
        "proactive_enabled": SETTINGS.get("proactive_enabled", True),
        "proactive_inactivity_enabled": SETTINGS.get("proactive_inactivity_enabled", True),
        "proactive_routine_enabled": SETTINGS.get("proactive_routine_enabled", True),
        "proactive_goal_enabled": SETTINGS.get("proactive_goal_enabled", True),
        "proactive_emotion_enabled": SETTINGS.get("proactive_emotion_enabled", True),
        "proactive_location_enabled": SETTINGS.get("proactive_location_enabled", True),
        "proactive_activity_enabled": SETTINGS.get("proactive_activity_enabled", True),
        "proactive_festival_enabled": SETTINGS.get("proactive_festival_enabled", True),
        "proactive_promise_enabled": SETTINGS.get("proactive_promise_enabled", True),
        "proactive_inactivity_hours": SETTINGS.get("proactive_inactivity_hours", 6),
        "proactive_config": SETTINGS.get("proactive_config", {}),
        "proactive_quiet_enabled": SETTINGS.get("proactive_quiet_enabled", True),
        "proactive_quiet_window": SETTINGS.get("proactive_quiet_window", "00:30-08:30"),
        "gift_prefer_html": SETTINGS.get("gift_prefer_html", True),
    }

@router.put("/api/settings")
async def update_settings(body: SettingsUpdate):
    if body.gemini_key is not None:
        SETTINGS["gemini_key"] = body.gemini_key
    if body.siliconflow_key is not None:
        SETTINGS["siliconflow_key"] = body.siliconflow_key
    if body.gemini_free_key is not None:
        SETTINGS["gemini_free_key"] = body.gemini_free_key
    if body.aipro_key is not None:
        SETTINGS["aipro_key"] = body.aipro_key
    if body.aipro_base_url is not None:
        SETTINGS["aipro_base_url"] = body.aipro_base_url
    if body.netease_music_u is not None:
        old_mu = SETTINGS.get("netease_music_u", "")
        SETTINGS["netease_music_u"] = body.netease_music_u
        if body.netease_music_u != old_mu:
            # MUSIC_U 变更，重新登录 pyncm
            try:
                from music import reload_login
                reload_login()
            except Exception:
                pass
    if body.proactive_enabled is not None:
        SETTINGS["proactive_enabled"] = bool(body.proactive_enabled)
    if body.proactive_inactivity_enabled is not None:
        SETTINGS["proactive_inactivity_enabled"] = bool(body.proactive_inactivity_enabled)
    if body.proactive_routine_enabled is not None:
        SETTINGS["proactive_routine_enabled"] = bool(body.proactive_routine_enabled)
    if body.proactive_goal_enabled is not None:
        SETTINGS["proactive_goal_enabled"] = bool(body.proactive_goal_enabled)
    if body.proactive_emotion_enabled is not None:
        SETTINGS["proactive_emotion_enabled"] = bool(body.proactive_emotion_enabled)
    if body.proactive_location_enabled is not None:
        SETTINGS["proactive_location_enabled"] = bool(body.proactive_location_enabled)
    if body.proactive_activity_enabled is not None:
        SETTINGS["proactive_activity_enabled"] = bool(body.proactive_activity_enabled)
    if body.proactive_festival_enabled is not None:
        SETTINGS["proactive_festival_enabled"] = bool(body.proactive_festival_enabled)
    if body.proactive_promise_enabled is not None:
        SETTINGS["proactive_promise_enabled"] = bool(body.proactive_promise_enabled)
    if body.proactive_inactivity_hours is not None:
        SETTINGS["proactive_inactivity_hours"] = max(1.0, min(72.0, float(body.proactive_inactivity_hours)))
    if body.proactive_config is not None and isinstance(body.proactive_config, dict):
        SETTINGS["proactive_config"] = body.proactive_config
    if body.proactive_quiet_enabled is not None:
        SETTINGS["proactive_quiet_enabled"] = bool(body.proactive_quiet_enabled)
    if body.proactive_quiet_window is not None:
        SETTINGS["proactive_quiet_window"] = (body.proactive_quiet_window or "").strip() or "00:30-08:30"
    if body.gift_prefer_html is not None:
        SETTINGS["gift_prefer_html"] = bool(body.gift_prefer_html)
    save_settings(SETTINGS)
    return {"ok": True}

# ── 温度设置 ──────────────────────────────────────
class TempUpdate(BaseModel):
    temperature: float

@router.put("/api/settings/temperature")
async def update_temperature(body: TempUpdate):
    SETTINGS["temperature"] = body.temperature
    save_settings(SETTINGS)
    return {"ok": True}

# ── 视频通话开关 ──────────────────────────────────
@router.get("/api/settings/video-call")
async def get_video_call_setting():
    return {"video_call_enabled": SETTINGS.get("video_call_enabled", True)}

class VideoCallToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/video-call")
async def update_video_call_setting(body: VideoCallToggle):
    SETTINGS["video_call_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "video_call_enabled": body.enabled}

# ── AI 生图开关 ───────────────────────────────────
@router.get("/api/settings/image-gen")
async def get_image_gen_setting():
    return {"image_gen_enabled": SETTINGS.get("image_gen_enabled", False)}

class ImageGenToggle(BaseModel):
    enabled: bool

@router.put("/api/settings/image-gen")
async def update_image_gen_setting(body: ImageGenToggle):
    SETTINGS["image_gen_enabled"] = body.enabled
    save_settings(SETTINGS)
    return {"ok": True, "image_gen_enabled": body.enabled}

# ── 世界书 ────────────────────────────────────────
class WorldBookUpdate(BaseModel):
    ai_persona: str = ""
    user_persona: str = ""
    system_prompt: str = ""
    ai_name: str = "AI"
    user_name: str = "你"

@router.get("/api/worldbook")
async def get_worldbook():
    # 兼容旧接口：返回“默认世界书”
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute(
            "SELECT * FROM worldbooks WHERE is_default=1 ORDER BY updated_at DESC LIMIT 1"
        )
        row = await cur.fetchone()
    if not row:
        return load_worldbook()
    return {
        "id": row["id"],
        "name": row["name"],
        "ai_persona": row["ai_persona"],
        "user_persona": row["user_persona"],
        "system_prompt": row["system_prompt"],
        "ai_name": row["ai_name"],
        "user_name": row["user_name"],
        "is_default": bool(row["is_default"]),
    }

@router.put("/api/worldbook")
async def update_worldbook(body: WorldBookUpdate):
    # 兼容旧接口：更新默认世界书内容
    now = time.time()
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute("SELECT id FROM worldbooks WHERE is_default=1 ORDER BY updated_at DESC LIMIT 1")
        row = await cur.fetchone()
        if row:
            await db.execute(
                """
                UPDATE worldbooks
                SET ai_persona=?, user_persona=?, system_prompt=?, ai_name=?, user_name=?, updated_at=?
                WHERE id=?
                """,
                (
                    body.ai_persona, body.user_persona, body.system_prompt,
                    body.ai_name, body.user_name, now, row["id"]
                ),
            )
        else:
            wb_id = f"wb_{int(now*1000)}"
            await db.execute(
                """
                INSERT INTO worldbooks
                (id, name, ai_name, user_name, ai_persona, user_persona, system_prompt, is_default, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    wb_id, "默认世界书", body.ai_name, body.user_name,
                    body.ai_persona, body.user_persona, body.system_prompt, 1, now, now
                ),
            )
        await db.commit()
    save_worldbook({"ai_persona": body.ai_persona, "user_persona": body.user_persona,
                    "system_prompt": body.system_prompt, "ai_name": body.ai_name, "user_name": body.user_name})
    return {"ok": True}


class WorldBookItemCreate(BaseModel):
    name: str = "新世界书"
    ai_persona: str = ""
    user_persona: str = ""
    system_prompt: str = ""
    ai_name: str = "AI"
    user_name: str = "你"


class WorldBookItemUpdate(BaseModel):
    name: Optional[str] = None
    ai_persona: Optional[str] = None
    user_persona: Optional[str] = None
    system_prompt: Optional[str] = None
    ai_name: Optional[str] = None
    user_name: Optional[str] = None


@router.get("/api/worldbooks")
async def list_worldbooks():
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute("SELECT * FROM worldbooks ORDER BY is_default DESC, updated_at DESC")
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/api/worldbooks/default")
async def get_default_worldbook():
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute("SELECT * FROM worldbooks WHERE is_default=1 ORDER BY updated_at DESC LIMIT 1")
        row = await cur.fetchone()
    return dict(row) if row else None


@router.post("/api/worldbooks")
async def create_worldbook_item(body: WorldBookItemCreate):
    now = time.time()
    wb_id = f"wb_{int(now*1000)}"
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO worldbooks
            (id, name, ai_name, user_name, ai_persona, user_persona, system_prompt, is_default, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                wb_id, body.name.strip() or "新世界书", body.ai_name, body.user_name,
                body.ai_persona, body.user_persona, body.system_prompt, 0, now, now
            ),
        )
        await db.commit()
    return {"ok": True, "id": wb_id}


@router.put("/api/worldbooks/{wb_id}")
async def update_worldbook_item(wb_id: str, body: WorldBookItemUpdate):
    updates = []
    vals = []
    for k in ("name", "ai_persona", "user_persona", "system_prompt", "ai_name", "user_name"):
        v = getattr(body, k)
        if v is not None:
            updates.append(f"{k}=?")
            vals.append(v)
    if not updates:
        return {"ok": True}
    updates.append("updated_at=?")
    vals.append(time.time())
    vals.append(wb_id)
    async with get_db() as db:
        await db.execute(f"UPDATE worldbooks SET {', '.join(updates)} WHERE id=?", tuple(vals))
        await db.commit()
    return {"ok": True}


@router.put("/api/worldbooks/{wb_id}/default")
async def set_worldbook_default(wb_id: str):
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute("SELECT * FROM worldbooks WHERE id=?", (wb_id,))
        row = await cur.fetchone()
        if not row:
            return Response(content=json.dumps({"error": "worldbook 不存在"}), status_code=404, media_type="application/json")
        await db.execute("UPDATE worldbooks SET is_default=0")
        await db.execute("UPDATE worldbooks SET is_default=1, updated_at=? WHERE id=?", (time.time(), wb_id))
        await db.commit()
    save_worldbook({
        "ai_persona": row["ai_persona"],
        "user_persona": row["user_persona"],
        "system_prompt": row["system_prompt"],
        "ai_name": row["ai_name"],
        "user_name": row["user_name"],
    })
    return {"ok": True}


@router.delete("/api/worldbooks/{wb_id}")
async def delete_worldbook_item(wb_id: str):
    async with get_db() as db:
        db.row_factory = __import__('aiosqlite').Row
        cur = await db.execute("SELECT is_default FROM worldbooks WHERE id=?", (wb_id,))
        row = await cur.fetchone()
        if not row:
            return {"ok": True}
        if int(row["is_default"]) == 1:
            return Response(content=json.dumps({"error": "默认世界书不能删除，请先设置其他默认项"}), status_code=400, media_type="application/json")
        # 被会话引用的世界书禁止删除
        cur = await db.execute("SELECT COUNT(*) AS c FROM conversations WHERE worldbook_id=?", (wb_id,))
        c = await cur.fetchone()
        if c and c["c"] > 0:
            return Response(content=json.dumps({"error": "该世界书正在被会话使用，无法删除"}), status_code=400, media_type="application/json")
        await db.execute("DELETE FROM worldbooks WHERE id=?", (wb_id,))
        await db.commit()
    return {"ok": True}

# ── 聊天状态 ──────────────────────────────────────
@router.get("/api/chat_status")
async def get_chat_status_api():
    return load_chat_status()

# ── TTS 语音合成 ──────────────────────────────────
class TTSRequest(BaseModel):
    text: str
    voice: str = ""
    msg_id: Optional[str] = None

@router.post("/api/tts")
async def tts_synthesize(body: TTSRequest):
    key = get_key("siliconflow")
    if not key:
        return Response(content=json.dumps({"error": "未配置硅基流动 API Key"}), status_code=400, media_type="application/json")
    if not body.text.strip():
        return Response(content=json.dumps({"error": "文本不能为空"}), status_code=400, media_type="application/json")
    if not body.voice:
        return Response(content=json.dumps({"error": "未选择语音"}), status_code=400, media_type="application/json")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.siliconflow.cn/v1/audio/speech",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "FunAudioLLM/CosyVoice2-0.5B",
                    "input": body.text.strip(),
                    "voice": body.voice,
                    "response_format": "mp3",
                    "speed": 1.0,
                    "gain": 0
                }
            )
        if resp.status_code != 200:
            return Response(content=json.dumps({"error": f"TTS API 错误: {resp.status_code}"}), status_code=502, media_type="application/json")
        audio_data = resp.content
        # 如果提供了 msg_id，将音频缓存到服务器
        if body.msg_id:
            import re
            safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', body.msg_id)
            if safe_id:
                cache_path = TTS_CACHE_DIR / f"{safe_id}.mp3"
                cache_path.write_bytes(audio_data)
        return Response(content=audio_data, media_type="audio/mpeg")
    except Exception as e:
        return Response(content=json.dumps({"error": str(e)}), status_code=500, media_type="application/json")

@router.head("/api/tts/audio/{msg_id}")
@router.get("/api/tts/audio/{msg_id}")
async def tts_audio(msg_id: str):
    import re
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', msg_id)
    if not safe_id:
        return Response(status_code=404)
    cache_path = TTS_CACHE_DIR / f"{safe_id}.mp3"
    if not cache_path.exists():
        return Response(status_code=404)
    return FileResponse(cache_path, media_type="audio/mpeg", filename=f"{safe_id}.mp3")

@router.get("/api/tts/voices")
async def tts_voice_list():
    key = get_key("siliconflow")
    if not key:
        return {"voices": [], "error": "未配置硅基流动 API Key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.siliconflow.cn/v1/audio/voice/list",
                headers={"Authorization": f"Bearer {key}"}
            )
        if resp.status_code != 200:
            return {"voices": [], "error": "获取音色列表失败"}
        data = resp.json()
        voices = data.get("result") or data.get("voices") or data.get("data") or []
        return {"voices": voices}
    except Exception as e:
        return {"voices": [], "error": str(e)}
