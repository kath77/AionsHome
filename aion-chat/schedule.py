from __future__ import annotations
"""
日程 / 闹铃管理器
- 后台线程每 30 秒扫描一次到期的闹铃
- 触发时组装 Prompt（世界书 + 记忆 + 上下文）调用 Core，与监控唤醒一致
- 所有日程持久化在 SQLite schedules 表，重启后自动恢复
"""

import asyncio, json, time, threading, logging, re
from datetime import datetime

import aiosqlite

from config import DB_PATH, DEFAULT_MODEL, load_worldbook, SETTINGS
from database import get_db
from ws import manager
from ai_providers import stream_ai
from memory import recall_memories
from music import search_songs, get_audio_url
from routes.music import MUSIC_CMD_PATTERN
from tts import TTSStreamer
from activity import get_activity_summary_for_prompt

log = logging.getLogger("schedule")

# ── 文本指令正则 ──────────────────────────────────
ALARM_CMD = re.compile(r"\[ALARM:(.+?)\|(.+?)\]")
REMINDER_CMD = re.compile(r"\[REMINDER:(.+?)\|(.+?)\]")
MONITOR_CMD = re.compile(r"\[Monitor:(.+?)\|(.+?)\]")
SCHEDULE_DEL_CMD = re.compile(r"\[SCHEDULE_DEL:(.+?)\]")
SCHEDULE_LIST_CMD = re.compile(r"\[SCHEDULE_LIST\]")


def _parse_dt(raw: str) -> str | None:
    """尝试把 AI 输出的时间字符串解析为 ISO 格式，失败返回 None"""
    raw = raw.strip()
    # ISO 格式的 T 分隔符统一替换为空格
    raw = raw.replace("T", " ")
    # 带时间的格式
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m-%d %H:%M",
        "%m-%d %H:%M",
        "%m/%d %H:%M",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    # 纯日期格式（REMINDER 可能不带时间）→ 默认 09:00
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m-%d",
        "%m/%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            dt = dt.replace(hour=9, minute=0)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


# ── ScheduleManager ───────────────────────────────
class ScheduleManager:
    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_proactive_check = 0.0

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        log.info("ScheduleManager started")

    def stop(self):
        self._running = False

    # ── 后台轮询 ──────────────────────────────────
    def _check_loop(self):
        while self._running:
            try:
                asyncio.run_coroutine_threadsafe(self._tick(), self._loop).result(timeout=60)
            except Exception as e:
                log.error("schedule tick error: %s", e)
            # 每 30 秒检查一次
            for _ in range(60):          # 30s = 60 × 0.5s
                if not self._running:
                    return
                time.sleep(0.5)

    async def _tick(self):
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM schedules WHERE status='active' AND type='alarm' AND trigger_at <= ?",
                (now_iso,),
            )
            due_alarms = [dict(r) for r in await cur.fetchall()]
            cur = await db.execute(
                "SELECT * FROM schedules WHERE status='active' AND type='monitor' AND trigger_at <= ?",
                (now_iso,),
            )
            due_monitors = [dict(r) for r in await cur.fetchall()]
        for item in due_alarms:
            await self._fire_alarm(item)
        for item in due_monitors:
            await self._fire_monitor(item)
        # 主动联系触发器（每 5 分钟检查一次）
        now_ts = time.time()
        if now_ts - self._last_proactive_check >= 300:
            self._last_proactive_check = now_ts
            try:
                await _run_proactive_triggers()
            except Exception as e:
                log.error("proactive trigger error: %s", e)

    # ── 触发闹铃 ─────────────────────────────────
    async def _fire_alarm(self, item: dict):
        sid = item["id"]
        content = item["content"]
        trigger_at = item["trigger_at"]
        log.info("firing alarm %s: %s @%s", sid, content, trigger_at)

        # 标记为已触发
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE schedules SET status='triggered' WHERE id=?", (sid,))
            await db.commit()

        # 广播给前端弹窗
        await manager.broadcast({
            "type": "schedule_alarm",
            "data": {"id": sid, "content": content, "trigger_at": trigger_at},
        })
        await manager.broadcast({"type": "schedule_changed"})

        # ── 组装 Prompt 调用 Core（与 camera._call_core 一致） ──
        wb = load_worldbook()
        user_name = wb.get("user_name", "你")
        ai_name = wb.get("ai_name", "AI")

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1")
            conv = await cur.fetchone()
            if not conv:
                return
            conv_id = conv["id"]
            model_key = conv["model"] or DEFAULT_MODEL

            cur = await db.execute(
                "SELECT role, content, attachments FROM messages WHERE conv_id=? "
                "AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 20",
                (conv_id,),
            )
            rows = await cur.fetchall()
            history = []
            for r in reversed(rows):
                d = dict(r)
                try:
                    d["attachments"] = json.loads(d.get("attachments") or "[]") if d.get("attachments") else []
                except Exception:
                    d["attachments"] = []
                history.append(d)

        # 世界书前缀
        prefix = []
        if wb.get("ai_persona"):
            prefix.append({"role": "user", "content": f"[系统设定 - AI人设]\n{wb['ai_persona']}"})
            prefix.append({"role": "assistant", "content": "收到，我会按照设定扮演角色。"})
        if wb.get("user_persona"):
            prefix.append({"role": "user", "content": f"[系统设定 - 用户信息]\n{wb['user_persona']}"})
            prefix.append({"role": "assistant", "content": "收到，我会记住你的信息。"})

        # 拼接当前时间（与正常发消息一致）
        now_str = datetime.now().strftime("%Y年%m月%d日  %H:%M:%S")
        if prefix:
            prefix[-1]["content"] += f"\n系统当前的准确时间是 {now_str}"

        # 注入系统能力提示（与 routes/chat.py 一致）
        abilities = []
        abilities.append("[MUSIC:歌曲名 歌手名] — 点歌/推荐音乐。系统自动展示播放卡片并自动播放，不要在指令外重复歌曲信息。可同时用多个。")
        abilities.append("[ALARM:YYYY-MM-DDTHH:MM|内容] — 设置闹铃，到时间系统会主动提醒用户。日期时间用ISO格式。")
        abilities.append("[REMINDER:YYYY-MM-DD|内容] — 设置日程提醒（不闹铃），你在合适时机自然提起即可。")
        abilities.append(f"[Monitor:YYYY-MM-DDTHH:MM|内容] — 设置定时监督。到时间后系统自动截取摄像头画面发送给你，你可以查看{user_name}的状态。")
        abilities.append("[SCHEDULE_DEL:日程id] — 删除指定日程/闹铃/定时监控。")
        ability_block = "[系统能力] 你可以在回复中根据对话氛围，善用以下指令：\n" + "\n".join(f"{i+1}. {a}" for i, a in enumerate(abilities))

        # 注入当前日程列表
        active_schedules = await get_active_schedules()
        schedule_text = build_schedule_prompt(active_schedules)
        ability_block += f"\n\n【当前日程列表】\n{schedule_text}"

        cap_idx = len(prefix) if prefix else 0
        history.insert(cap_idx, {"role": "user", "content": ability_block})
        history.insert(cap_idx + 1, {"role": "assistant", "content": "好的，需要时我会使用这些指令。"})

        # 触发提示词
        trigger_prompt = (
            f"[日程闹铃触发]\n"
            f"日程内容：{trigger_at} — {content}\n"
            f"现在时间已经到了（当前 {now_str}），请提醒【{user_name}】。"
        )

        # 记忆召回
        recalled, _ = await recall_memories(trigger_prompt[:300])
        mem_inject = []
        if recalled:
            mem_lines = "\n".join([f"- {m['content']}" for m in recalled])
            mem_inject = [
                {"role": "user", "content": f"[相关记忆]\n你脑海中与当前话题相关的记忆：\n{mem_lines}"},
                {"role": "assistant", "content": "收到，我会自然地参考这些记忆。"},
            ]

        messages = prefix + mem_inject + history + [{"role": "user", "content": trigger_prompt}]

        # 预生成 ai_msg_id（TTS 分段文件命名需要）
        ai_msg_id = f"msg_{int(time.time()*1000)}_sa"

        # TTS：检查是否有前端开了 TTS
        alarm_tts = None
        if manager.any_tts_enabled():
            tts_voice = manager.get_tts_voice()
            if tts_voice:
                alarm_tts = TTSStreamer(ai_msg_id, tts_voice, manager)

        full_text = ""
        try:
            _temp = SETTINGS.get("temperature")
            async for chunk in stream_ai(messages, model_key, temperature=_temp):
                full_text += chunk
                if alarm_tts:
                    alarm_tts.feed(chunk)
        except Exception as e:
            full_text = f"[闹铃提醒回复失败] {e}"

        if not full_text.strip():
            return

        # 检测 [MUSIC:xxx] 指令 → 搜索歌曲并推送卡片数据（自动播放）
        music_matches = MUSIC_CMD_PATTERN.findall(full_text)
        music_cards = []
        if music_matches:
            for keyword in music_matches:
                keyword = keyword.strip()
                try:
                    results = search_songs(keyword, limit=5)
                    if results:
                        song = results[0]
                        song["audio_url"] = get_audio_url(song["id"])
                        song["candidates"] = results[1:4]
                        music_cards.append(song)
                except Exception:
                    pass
            full_text = MUSIC_CMD_PATTERN.sub("", full_text).strip()

        # 处理回复中可能包含的日程指令
        full_text = await process_schedule_commands(full_text, conv_id)

        # 将音乐点歌信息存入 attachments
        music_atts = [{"type": "music", "name": s["name"], "artist": s["artist"], "id": s["id"]} for s in music_cards] if music_cards else []
        att_json = json.dumps(music_atts, ensure_ascii=False) if music_atts else "[]"

        # 插入系统提示 + AI 回复
        now = time.time()
        sys_msg_id = f"msg_{int(now*1000)}_st"
        sys_content = f"⏰ 日程闹铃触发：{content}"
        async with get_db() as db:
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
                (sys_msg_id, conv_id, "system", sys_content, now, "[]"),
            )
            await db.commit()
        sys_msg = {"id": sys_msg_id, "conv_id": conv_id, "role": "system",
                   "content": sys_content, "created_at": now, "attachments": []}
        await manager.broadcast({"type": "msg_created", "data": sys_msg})

        now2 = time.time()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
                (ai_msg_id, conv_id, "assistant", full_text, now2, att_json),
            )
            await db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now2, conv_id))
            await db.commit()
        ai_msg = {"id": ai_msg_id, "conv_id": conv_id, "role": "assistant",
                  "content": full_text, "created_at": now2, "attachments": music_atts}
        await manager.broadcast({"type": "msg_created", "data": ai_msg})

        # 刷新 TTS 剩余文本
        if alarm_tts:
            try:
                await alarm_tts.flush()
            except Exception:
                pass

        # 推送音乐卡片（带 autoplay 标记，前端自动播放）
        if music_cards:
            music_data = {'type': 'music', 'msg_id': ai_msg_id, 'cards': music_cards, 'autoplay': True}
            await manager.broadcast({"type": "music", "data": music_data})

        from routes.files import export_conversation
        await export_conversation(conv_id)

    # ── 触发定时监控 ─────────────────────────────
    async def _fire_monitor(self, item: dict):
        sid = item["id"]
        content = item["content"]
        trigger_at = item["trigger_at"]
        log.info("firing monitor %s: %s @%s", sid, content, trigger_at)

        # 标记为已触发
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE schedules SET status='triggered' WHERE id=?", (sid,))
            await db.commit()
        await manager.broadcast({"type": "schedule_changed"})

        # 尝试截图（摄像头可能未开启）
        from camera import cam
        fname = None
        if cam.running:
            # 播放提示音 + 5秒延迟，给用户反应时间
            await manager.broadcast({"type": "monitor_alert", "data": {"content": content}})
            await asyncio.sleep(5)

            jpg_bytes = cam.get_frame_jpeg()
            if jpg_bytes:
                from config import UPLOADS_DIR, SCREENSHOTS_DIR
                ts = time.strftime("%Y%m%d_%H%M%S")
                fname = f"monitor_{ts}.jpg"
                fpath = UPLOADS_DIR / fname
                fpath.write_bytes(jpg_bytes)

                # 同时保存到 screenshots 目录
                SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                (SCREENSHOTS_DIR / fname).write_bytes(jpg_bytes)

        # 获取最新对话
        wb = load_worldbook()
        user_name = wb.get("user_name", "你")
        ai_name = wb.get("ai_name", "AI")

        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1")
            conv = await cur.fetchone()
            if not conv:
                return
            conv_id = conv["id"]
            model_key = conv["model"] or DEFAULT_MODEL

            cur = await db.execute(
                "SELECT role, content, attachments FROM messages WHERE conv_id=? "
                "AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 20",
                (conv_id,),
            )
            rows = await cur.fetchall()
            history = []
            for r in reversed(rows):
                d = dict(r)
                try:
                    d["attachments"] = json.loads(d.get("attachments") or "[]") if d.get("attachments") else []
                except Exception:
                    d["attachments"] = []
                # 历史消息不带图片
                d["attachments"] = []
                history.append(d)

        # 世界书前缀
        prefix = []
        if wb.get("ai_persona"):
            prefix.append({"role": "user", "content": f"[系统设定 - AI人设]\n{wb['ai_persona']}"})
            prefix.append({"role": "assistant", "content": "收到，我会按照设定扮演角色。"})
        if wb.get("user_persona"):
            prefix.append({"role": "user", "content": f"[系统设定 - 用户信息]\n{wb['user_persona']}"})
            prefix.append({"role": "assistant", "content": "收到，我会记住你的信息。"})

        now_str = datetime.now().strftime("%Y年%m月%d日  %H:%M:%S")
        if prefix:
            prefix[-1]["content"] += f"\n系统当前的准确时间是 {now_str}"

        # 注入系统能力提示（与 routes/chat.py 一致）
        abilities = []
        abilities.append("[MUSIC:歌曲名 歌手名] — 点歌/推荐音乐。系统自动展示播放卡片并自动播放，不要在指令外重复歌曲信息。可同时用多个。")
        abilities.append("[ALARM:YYYY-MM-DDTHH:MM|内容] — 设置闹铃，到时间系统会主动提醒用户。日期时间用ISO格式。")
        abilities.append("[REMINDER:YYYY-MM-DD|内容] — 设置日程提醒（不闹铃），你在合适时机自然提起即可。")
        abilities.append(f"[Monitor:YYYY-MM-DDTHH:MM|内容] — 设置定时监督。到时间后系统自动截取摄像头画面发送给你，你可以查看{user_name}的状态。")
        abilities.append("[SCHEDULE_DEL:日程id] — 删除指定日程/闹铃/定时监控。")
        ability_block = "[系统能力] 你可以在回复中根据对话氛围，善用以下指令：\n" + "\n".join(f"{i+1}. {a}" for i, a in enumerate(abilities))

        # 注入当前日程列表
        active_schedules = await get_active_schedules()
        schedule_text = build_schedule_prompt(active_schedules)
        ability_block += f"\n\n【当前日程列表】\n{schedule_text}"

        cap_idx = len(prefix) if prefix else 0
        history.insert(cap_idx, {"role": "user", "content": ability_block})
        history.insert(cap_idx + 1, {"role": "assistant", "content": "好的，需要时我会使用这些指令。"})

        # 获取最近 1 小时的设备活动摘要（6 条）
        activity_summary_text = ""
        try:
            from activity import get_activity_summary_for_prompt
            activity_summary_text = get_activity_summary_for_prompt(6)
        except Exception:
            pass

        # 触发提示词
        trigger_prompt = (
            f"[定时监控触发]\n"
            f"你之前设置了在 {trigger_at.replace('T', ' ')} 查看【{user_name}】的状态。\n"
            f"监控目的：{content}\n"
        )
        if fname:
            trigger_prompt += f"这是系统在当前时间（{now_str}）自动从摄像头截取的实时画面。\n"
        else:
            trigger_prompt += f"当前时间是{now_str}，摄像头未开启，无法获取画面。\n"
        if activity_summary_text:
            trigger_prompt += (
                f"\n以下是{user_name}过去一小时的设备使用动态（手机/电脑应用使用情况，每10分钟一条摘要）：\n"
                f"{activity_summary_text}\n"
            )
        if fname:
            trigger_prompt += f"\n请根据画面内容、设备活动动态和之前的对话上下文，自然地回应。"
        else:
            trigger_prompt += f"\n请根据设备活动动态和之前的对话上下文，自然地回应。"

        trigger_msg = {"role": "user", "content": trigger_prompt}
        if fname:
            trigger_msg["attachments"] = [f"/uploads/{fname}"]
        messages = prefix + history + [trigger_msg]

        # 预生成 ai_msg_id（TTS 分段文件命名需要）
        ai_msg_id = f"msg_{int(time.time()*1000)}_sm"

        # TTS：检查是否有前端开了 TTS
        monitor_tts = None
        if manager.any_tts_enabled():
            tts_voice = manager.get_tts_voice()
            if tts_voice:
                monitor_tts = TTSStreamer(ai_msg_id, tts_voice, manager)

        full_text = ""
        try:
            _temp = SETTINGS.get("temperature")
            async for chunk in stream_ai(messages, model_key, temperature=_temp):
                full_text += chunk
                if monitor_tts:
                    monitor_tts.feed(chunk)
        except Exception as e:
            full_text = f"[定时监控回复失败] {e}"

        if not full_text.strip():
            return

        # 检测 [MUSIC:xxx] 指令
        music_matches = MUSIC_CMD_PATTERN.findall(full_text)
        music_cards = []
        if music_matches:
            for keyword in music_matches:
                keyword = keyword.strip()
                try:
                    results = search_songs(keyword, limit=5)
                    if results:
                        song = results[0]
                        song["audio_url"] = get_audio_url(song["id"])
                        song["candidates"] = results[1:4]
                        music_cards.append(song)
                except Exception:
                    pass
            full_text = MUSIC_CMD_PATTERN.sub("", full_text).strip()

        # 处理回复中可能包含的日程指令
        full_text = await process_schedule_commands(full_text, conv_id)

        # 将音乐点歌信息存入 attachments
        music_atts = [{"type": "music", "name": s["name"], "artist": s["artist"], "id": s["id"]} for s in music_cards] if music_cards else []
        att_json = json.dumps(music_atts, ensure_ascii=False) if music_atts else "[]"

        # 插入系统提示 + AI 回复
        now = time.time()
        sys_msg_id = f"msg_{int(now*1000)}_sm"
        sys_content = f"{ai_name}查看了监控"
        async with get_db() as db:
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
                (sys_msg_id, conv_id, "system", sys_content, now, "[]"),
            )
            await db.commit()
        sys_msg = {"id": sys_msg_id, "conv_id": conv_id, "role": "system",
                   "content": sys_content, "created_at": now, "attachments": []}
        await manager.broadcast({"type": "msg_created", "data": sys_msg})

        now2 = time.time()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
                (ai_msg_id, conv_id, "assistant", full_text, now2, att_json),
            )
            await db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now2, conv_id))
            await db.commit()
        ai_msg = {"id": ai_msg_id, "conv_id": conv_id, "role": "assistant",
                  "content": full_text, "created_at": now2, "attachments": music_atts}
        await manager.broadcast({"type": "msg_created", "data": ai_msg})

        # 刷新 TTS 剩余文本
        if monitor_tts:
            try:
                await monitor_tts.flush()
            except Exception:
                pass

        # 推送音乐卡片（带 autoplay 标记）
        if music_cards:
            music_data = {'type': 'music', 'msg_id': ai_msg_id, 'cards': music_cards, 'autoplay': True}
            await manager.broadcast({"type": "music", "data": music_data})

        from routes.files import export_conversation
        await export_conversation(conv_id)


# ── 指令解析（在 AI 回复完成后调用） ──────────────
async def process_schedule_commands(full_text: str, conv_id: str = None) -> str:
    """
    检测并处理 AI 回复中的日程指令，返回 strip 后的文本。
    即使 AI 格式有误也不抛异常，静默跳过。
    """
    text = full_text
    wb = load_worldbook()
    ai_name = wb.get("ai_name", "AI")

    # [ALARM:datetime|content]
    for match in ALARM_CMD.finditer(full_text):
        try:
            raw_dt, content = match.group(1), match.group(2)
            dt = _parse_dt(raw_dt)
            log.info("ALARM detected: raw_dt=%s parsed=%s content=%s", raw_dt, dt, content)
            if dt and content.strip():
                await _add_schedule("alarm", dt, content.strip())
                if conv_id:
                    await _sys_msg(conv_id, f"{ai_name} 设置了 {dt.replace('T', ' ')} 的闹铃：{content.strip()}")
            else:
                log.warning("ALARM skipped: dt=%s content=%s", dt, content)
        except Exception as e:
            log.error("ALARM processing error: %s", e)
    text = ALARM_CMD.sub("", text)

    # [REMINDER:date|content]
    for match in REMINDER_CMD.finditer(full_text):
        try:
            raw_dt, content = match.group(1), match.group(2)
            dt = _parse_dt(raw_dt)
            log.info("REMINDER detected: raw_dt=%s parsed=%s content=%s", raw_dt, dt, content)
            if dt and content.strip():
                await _add_schedule("reminder", dt, content.strip())
                if conv_id:
                    await _sys_msg(conv_id, f"{ai_name} 设置了 {dt.replace('T', ' ')} 的日程：{content.strip()}")
            else:
                log.warning("REMINDER skipped: dt=%s content=%s", dt, content)
        except Exception as e:
            log.error("REMINDER processing error: %s", e)
    text = REMINDER_CMD.sub("", text)

    # [Monitor:datetime|content]
    for match in MONITOR_CMD.finditer(full_text):
        try:
            raw_dt, content = match.group(1), match.group(2)
            dt = _parse_dt(raw_dt)
            log.info("MONITOR detected: raw_dt=%s parsed=%s content=%s", raw_dt, dt, content)
            if dt and content.strip():
                await _add_schedule("monitor", dt, content.strip())
                if conv_id:
                    await _sys_msg(conv_id, f"{ai_name} 设置了 {dt.replace('T', ' ')} 的查岗：{content.strip()}")
            else:
                log.warning("MONITOR skipped: dt=%s content=%s", dt, content)
        except Exception as e:
            log.error("MONITOR processing error: %s", e)
    text = MONITOR_CMD.sub("", text)

    # [SCHEDULE_DEL:id]
    for match in SCHEDULE_DEL_CMD.finditer(full_text):
        try:
            sid = match.group(1).strip()
            if sid:
                info = await _get_schedule_info(sid)
                await _del_schedule(sid)
                if conv_id and info:
                    type_labels = {"alarm": "闹铃", "reminder": "日程", "monitor": "定时监控"}
                    label = type_labels.get(info["type"], "日程")
                    await _sys_msg(conv_id, f"{ai_name} 取消了 {info['trigger_at'].replace('T', ' ')} 的{label}：{info['content']}")
        except Exception as e:
            log.error("SCHEDULE_DEL processing error: %s", e)
    text = SCHEDULE_DEL_CMD.sub("", text)

    # [SCHEDULE_LIST] → 不需要实际操作，仅 strip
    text = SCHEDULE_LIST_CMD.sub("", text)

    return text.strip()


async def _sys_msg(conv_id: str, content: str):
    """插入一条系统消息并广播"""
    now = time.time()
    msg_id = f"msg_{int(now*1000)}_ss"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
            (msg_id, conv_id, "system", content, now, "[]"),
        )
        await db.commit()
    msg = {"id": msg_id, "conv_id": conv_id, "role": "system",
           "content": content, "created_at": now, "attachments": []}
    await manager.broadcast({"type": "msg_created", "data": msg})


async def _get_schedule_info(sid: str) -> dict | None:
    """查询日程详情（用于删除时生成系统消息）"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT type, trigger_at, content FROM schedules WHERE id=?", (sid,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def _add_schedule(stype: str, trigger_at: str, content: str):
    sid = f"sch_{int(time.time()*1000)}"
    now = time.time()
    trigger_at = trigger_at.replace("T", " ")
    async with get_db() as db:
        await db.execute(
            "INSERT INTO schedules (id, type, trigger_at, content, created_at, status) VALUES (?,?,?,?,?,?)",
            (sid, stype, trigger_at, content, now, "active"),
        )
        await db.commit()
    await manager.broadcast({"type": "schedule_changed"})


async def _del_schedule(sid: str):
    async with get_db() as db:
        await db.execute("UPDATE schedules SET status='cancelled' WHERE id=?", (sid,))
        await db.commit()
    await manager.broadcast({"type": "schedule_changed"})


# ── 获取活跃日程（供 prompt 注入） ────────────────
async def get_active_schedules() -> list[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, type, trigger_at, content FROM schedules WHERE status='active' ORDER BY trigger_at",
        )
        return [dict(r) for r in await cur.fetchall()]


def build_schedule_prompt(schedules: list[dict]) -> str:
    """构建注入 prompt 的日程列表文本"""
    if not schedules:
        return "暂无日程"
    type_map = {"alarm": ("🔔", "闹铃"), "reminder": ("📋", "日程"), "monitor": ("👁", "监督")}
    lines = []
    for s in schedules:
        icon, label = type_map.get(s["type"], ("📋", "日程"))
        lines.append(f"- {icon} {label} #{s['id']}: {s['trigger_at'].replace('T', ' ')} — {s['content']}")
    return "\n".join(lines)


# ── 单例 ──────────────────────────────────────────
schedule_mgr = ScheduleManager()


async def _latest_conv_and_model():
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, model FROM conversations ORDER BY updated_at DESC LIMIT 1")
        row = await cur.fetchone()
        if not row:
            return None, DEFAULT_MODEL
        return row["id"], (row["model"] or DEFAULT_MODEL)


async def _last_msg_ts(role: str = "user") -> float:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT created_at FROM messages WHERE role=? ORDER BY created_at DESC LIMIT 1", (role,))
        row = await cur.fetchone()
        return float(row["created_at"]) if row else 0.0


async def _fired_recent(trigger_key: str, cooldown_sec: int) -> bool:
    cutoff = time.time() - cooldown_sec
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT 1 FROM proactive_events WHERE trigger_key=? AND fired_at>=? ORDER BY fired_at DESC LIMIT 1",
            (trigger_key, cutoff),
        )
        return (await cur.fetchone()) is not None


async def _mark_fired(trigger_key: str, conv_id: str, note: str = ""):
    now = time.time()
    eid = f"pe_{int(now*1000)}"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO proactive_events (id, trigger_key, fired_at, conv_id, note) VALUES (?,?,?,?,?)",
            (eid, trigger_key, now, conv_id, note[:300]),
        )
        await db.commit()

def _default_proactive_config() -> dict:
    return {
        "inactivity": {"hours": 6, "cooldown_min": 720},
        "routine": {"cooldown_hours": 24, "morning_window": "07:30-10:30", "noon_window": "11:30-14:00", "night_window": "22:00-23:59"},
        "goal": {"cooldown_min": 480},
        "emotion": {"cooldown_min": 360, "msg_range_min": 3, "msg_range_max": 12, "keywords": "好累,烦,崩溃,难受,睡不着,焦虑,压力,抑郁"},
        "location": {"cooldown_hours": 24, "status_fresh_min": 30, "require_recent_state_change": True, "state_change_within_hours": 2},
        "activity": {"cooldown_min": 360, "summary_slots": 6, "keywords": "连续,长时间,系统设置,刷视频,游戏,停留"},
        "festival": {"cooldown_min": 525600, "custom_dates": "01-01,02-14,03-08,05-01,05-20,05-21,06-01,08-19,10-01,12-24,12-25,12-31"},
        "promise": {"cooldown_min": 480, "msg_range_min": 10, "msg_range_max": 40, "min_hours_since_promise": 2, "keywords": "我会,提醒,问你,再联系你,等会儿我来问你,稍后我提醒你"},
    }

def _get_proactive_cfg() -> dict:
    base = _default_proactive_config()
    custom = SETTINGS.get("proactive_config", {}) or {}
    if not isinstance(custom, dict):
        return base
    for k, v in custom.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
    return base

def _in_time_window(window_text: str) -> bool:
    try:
        now = datetime.now().strftime("%H:%M")
        if not window_text or "-" not in window_text:
            return True
        start, end = [x.strip() for x in window_text.split("-", 1)]
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end
    except Exception:
        return True

def _kw_pattern(csv_text: str) -> str:
    kws = [k.strip() for k in (csv_text or "").split(",") if k.strip()]
    return "(" + "|".join(re.escape(k) for k in kws) + ")" if kws else ""

def _cooldown_sec(cfg: dict, default_hours: float) -> int:
    """优先读取 cooldown_hours；兼容旧 cooldown_min。"""
    if "cooldown_hours" in cfg:
        h = float(cfg.get("cooldown_hours", default_hours))
    elif "cooldown_min" in cfg:
        h = float(cfg.get("cooldown_min", default_hours * 60)) / 60.0
    else:
        h = default_hours
    h = max(0.1, min(24 * 365, h))
    return int(h * 3600)


async def _send_proactive_message(conv_id: str, model_key: str, trigger_name: str, trigger_prompt: str):
    wb = load_worldbook()
    user_name = wb.get("user_name", "你")
    prefix = []
    if wb.get("ai_persona"):
        prefix.append({"role": "user", "content": f"[系统设定 - AI人设]\n{wb['ai_persona']}"})
        prefix.append({"role": "assistant", "content": "收到，我会按照设定扮演角色。"})
    if wb.get("user_persona"):
        prefix.append({"role": "user", "content": f"[系统设定 - 用户信息]\n{wb['user_persona']}"})
        prefix.append({"role": "assistant", "content": "收到，我会记住你的信息。"})
    if wb.get("system_prompt"):
        prefix.append({"role": "user", "content": f"[系统提示]\n{wb['system_prompt']}"})
        prefix.append({"role": "assistant", "content": "收到，我会遵循这些规则。"})

    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content, attachments FROM messages WHERE conv_id=? AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 12",
            (conv_id,),
        )
        rows = await cur.fetchall()
    history = []
    for r in reversed(rows):
        d = dict(r)
        try:
            d["attachments"] = json.loads(d.get("attachments") or "[]") if d.get("attachments") else []
        except Exception:
            d["attachments"] = []
        history.append(d)
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")
    prompt = (
        f"[主动联系触发:{trigger_name}]\n当前时间：{now_str}\n"
        f"请你主动联系{user_name}，语气自然、简短、关心，不要提“系统触发/规则/监控”。\n"
        f"{trigger_prompt}\n"
        "限制：1~2句话，不要连续追问，不要命令口吻。"
    )
    messages = prefix + history + [{"role": "user", "content": prompt}]

    full_text = ""
    try:
        _temp = SETTINGS.get("temperature")
        async for chunk in stream_ai(messages, model_key, temperature=_temp):
            full_text += chunk
    except Exception as e:
        log.error("proactive stream error: %s", e)
        return False
    full_text = full_text.strip()
    if not full_text:
        return False

    now = time.time()
    msg_id = f"msg_{int(now*1000)}_pro"
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (id, conv_id, role, content, created_at, attachments) VALUES (?,?,?,?,?,?)",
            (msg_id, conv_id, "assistant", full_text, now, "[]"),
        )
        await db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        await db.commit()
    await manager.broadcast({"type": "msg_created", "data": {
        "id": msg_id, "conv_id": conv_id, "role": "assistant", "content": full_text, "created_at": now, "attachments": []
    }})
    from routes.files import export_conversation
    await export_conversation(conv_id)
    return True


async def _run_proactive_triggers():
    if not SETTINGS.get("proactive_enabled", True):
        return
    if SETTINGS.get("proactive_quiet_enabled", True):
        qwin = str(SETTINGS.get("proactive_quiet_window", "00:30-08:30"))
        if _in_time_window(qwin):
            return
    conv_id, model_key = await _latest_conv_and_model()
    if not conv_id:
        return

    now = time.time()
    last_user = await _last_msg_ts("user")
    if last_user <= 0:
        return
    elapsed_h = (now - last_user) / 3600.0

    cfg = _get_proactive_cfg()

    # 1) 长时间无互动
    if SETTINGS.get("proactive_inactivity_enabled", True):
        c = cfg["inactivity"]
        h = float(c.get("hours", SETTINGS.get("proactive_inactivity_hours", 6)))
        cd = _cooldown_sec(c, 12)
        if elapsed_h >= h and not await _fired_recent("inactivity", cd):
            if await _send_proactive_message(conv_id, model_key, "长时间无互动", f"对方大约{int(elapsed_h)}小时没有发消息了。"):
                await _mark_fired("inactivity", conv_id, f"{elapsed_h:.1f}h")
                return

    # 2) 作息节点
    if SETTINGS.get("proactive_routine_enabled", True):
        c = cfg["routine"]
        hhmm = datetime.now().strftime("%H:%M")
        day = datetime.now().strftime("%Y%m%d")
        slot = None
        if _in_time_window(str(c.get("morning_window", "07:30-10:30"))):
            slot = "morning"
        elif _in_time_window(str(c.get("noon_window", "11:30-14:00"))):
            slot = "noon"
        elif _in_time_window(str(c.get("night_window", "22:00-23:59"))):
            slot = "night"
        if slot:
            key = f"routine_{slot}_{day}"
            cd = _cooldown_sec(c, 48)
            if not await _fired_recent(key, cd):
                tip = {"morning": "早安问候", "noon": "午间关心", "night": "晚安关心"}[slot]
                if await _send_proactive_message(conv_id, model_key, "作息节点", tip):
                    await _mark_fired(key, conv_id, slot)
                    return

    # 3) 目标跟进（未完成记忆）
    if SETTINGS.get("proactive_goal_enabled", True):
        c = cfg["goal"]
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT content FROM memories WHERE unresolved=1 ORDER BY importance DESC, created_at DESC LIMIT 1")
            row = await cur.fetchone()
        cd = _cooldown_sec(c, 8)
        if row and not await _fired_recent("goal_followup", cd):
            if await _send_proactive_message(conv_id, model_key, "目标跟进", f"之前有未完成事项：{row['content']}，温柔问一下进展。"):
                await _mark_fired("goal_followup", conv_id, row["content"])
                return

    # 4) 情绪风险
    if SETTINGS.get("proactive_emotion_enabled", True):
        c = cfg["emotion"]
        mn = max(1, int(c.get("msg_range_min", 3)))
        mx = max(mn, int(c.get("msg_range_max", 12)))
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT content FROM messages WHERE role='user' ORDER BY created_at DESC LIMIT ?", (mx,))
            rows = await cur.fetchall()
        if len(rows) >= mn:
            text = "\n".join([r["content"] for r in rows if r["content"]])
            pat = _kw_pattern(str(c.get("keywords", "")))
            cd = _cooldown_sec(c, 6)
            if pat and re.search(pat, text) and not await _fired_recent("emotion_risk", cd):
                if await _send_proactive_message(conv_id, model_key, "情绪关怀", "对方最近情绪可能不太好，先共情再给一个很小的建议。"):
                    await _mark_fired("emotion_risk", conv_id, "keywords")
                    return

    # 5) 位置场景
    if SETTINGS.get("proactive_location_enabled", True):
        c = cfg["location"]
        try:
            from location import load_location_status, load_location_config
            cfg_loc = load_location_config()
            st = load_location_status()
            is_enabled = cfg_loc.get("enabled")
            is_outside = st.get("state") == "outside"
            updated_at = float(st.get("updated_at", 0) or 0)
            state_changed_at = float(st.get("state_changed_at", 0) or 0)
            fresh_min = max(1, int(c.get("status_fresh_min", 30)))
            require_recent_change = bool(c.get("require_recent_state_change", True))
            change_within_h = max(0.1, float(c.get("state_change_within_hours", 2)))
            fresh_ok = updated_at > 0 and (time.time() - updated_at) <= fresh_min * 60
            change_ok = (not require_recent_change) or (
                state_changed_at > 0 and (time.time() - state_changed_at) <= change_within_h * 3600
            )
            if is_enabled and is_outside and fresh_ok and change_ok:
                key = "location_outside_" + datetime.now().strftime("%Y%m%d")
                cd = _cooldown_sec(c, 24)
                if not await _fired_recent(key, cd):
                    if await _send_proactive_message(conv_id, model_key, "外出场景", f"对方当前在外出状态，地址信息：{st.get('address','未知')}。"):
                        await _mark_fired(key, conv_id, "outside")
                        return
        except Exception:
            pass

    # 6) 设备高强度
    if SETTINGS.get("proactive_activity_enabled", True):
        c = cfg["activity"]
        try:
            slots = max(1, min(12, int(c.get("summary_slots", 6))))
            summary = get_activity_summary_for_prompt(slots) or ""
            pat = _kw_pattern(str(c.get("keywords", "")))
            cd = _cooldown_sec(c, 6)
            if pat and re.search(pat, summary) and not await _fired_recent("activity_overuse", cd):
                if await _send_proactive_message(conv_id, model_key, "设备高强度使用", "最近设备使用时长偏长，提醒喝水和活动肩颈。"):
                    await _mark_fired("activity_overuse", conv_id, "activity")
                    return
        except Exception:
            pass

    # 7) 节日触发
    if SETTINGS.get("proactive_festival_enabled", True):
        c = cfg["festival"]
        md = datetime.now().strftime("%m-%d")
        festivals = {"01-01": "新年", "02-14": "情人节", "05-20": "520", "10-01": "国庆节", "12-24": "平安夜", "12-25": "圣诞节"}
        custom = [x.strip() for x in str(c.get("custom_dates", "")).split(",") if x.strip()]
        if md in festivals or md in custom:
            fest_name = festivals.get(md, f"{md}节日")
            key = "festival_" + md + "_" + datetime.now().strftime("%Y")
            cd = _cooldown_sec(c, 24 * 365)
            if not await _fired_recent(key, cd):
                if await _send_proactive_message(conv_id, model_key, "节日问候", f"今天是{fest_name}，发送一句有仪式感的问候。"):
                    await _mark_fired(key, conv_id, fest_name)
                    return

    # 8) 承诺跟进（弱规则）
    if SETTINGS.get("proactive_promise_enabled", True):
        c = cfg["promise"]
        mn = max(1, int(c.get("msg_range_min", 10)))
        mx = max(mn, int(c.get("msg_range_max", 40)))
        minh = float(c.get("min_hours_since_promise", 2))
        pat = _kw_pattern(str(c.get("keywords", "")))
        if not pat:
            return
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT content, created_at FROM messages WHERE role='assistant' ORDER BY created_at DESC LIMIT ?", (mx,))
            rows = await cur.fetchall()
        if len(rows) < mn:
            return
        cd = _cooldown_sec(c, 8)
        for r in rows:
            txt = r["content"] or ""
            if re.search(pat, txt):
                if now - float(r["created_at"]) >= minh * 3600 and not await _fired_recent("promise_followup", cd):
                    if await _send_proactive_message(conv_id, model_key, "承诺跟进", "你之前答应过稍后跟进，现在自然回访一下。"):
                        await _mark_fired("promise_followup", conv_id, txt[:80])
                        return
                break
