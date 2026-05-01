from __future__ import annotations
"""
数据库初始化与连接
"""

import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT 'gemini-3-flash',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        try:
            await db.execute("ALTER TABLE conversations ADD COLUMN worldbook_id TEXT")
        except:
            pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conv_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)
        await db.execute("PRAGMA foreign_keys = ON")
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN attachments TEXT DEFAULT ''")
        except:
            pass
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN starred INTEGER DEFAULT 0")
        except:
            pass
        # 性能索引
        await db.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conv_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC)")
        # ── 世界书表（多世界书） ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS worldbooks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ai_name TEXT NOT NULL DEFAULT 'AI',
                user_name TEXT NOT NULL DEFAULT '你',
                ai_persona TEXT NOT NULL DEFAULT '',
                user_persona TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL DEFAULT '',
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_worldbooks_default ON worldbooks(is_default)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS proactive_events (
                id TEXT PRIMARY KEY,
                trigger_key TEXT NOT NULL,
                fired_at REAL NOT NULL,
                conv_id TEXT,
                note TEXT DEFAULT ''
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_proactive_key_time ON proactive_events(trigger_key, fired_at DESC)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                type TEXT DEFAULT 'event',
                created_at REAL NOT NULL,
                source_conv TEXT,
                embedding BLOB
            )
        """)
        # memories 表新增字段（向后兼容迁移）
        for col, defn in [
            ("keywords", "TEXT DEFAULT ''"),
            ("importance", "REAL DEFAULT 0.5"),
            ("source_start_ts", "REAL"),
            ("source_end_ts", "REAL"),
            ("unresolved", "INTEGER DEFAULT 0"),
            ("source_msg_id", "TEXT"),
        ]:
            try:
                await db.execute(f"ALTER TABLE memories ADD COLUMN {col} {defn}")
            except:
                pass
        # ── 日程/闹铃表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                trigger_at TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_schedules_status ON schedules(status, trigger_at)")
        # ── 心语表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS heart_whispers (
                id TEXT PRIMARY KEY,
                conv_id TEXT,
                msg_id TEXT,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_heart_whispers_created ON heart_whispers(created_at DESC)")
        # ── 书籍表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS books (
                book_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                author TEXT DEFAULT '未知作者',
                cover_path TEXT,
                total_chapters INTEGER DEFAULT 0,
                current_chapter INTEGER DEFAULT 0,
                current_paragraph INTEGER DEFAULT 0,
                import_time REAL NOT NULL
            )
        """)
        # ── 书籍章节表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS book_chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                title TEXT,
                html_content TEXT,
                text_content TEXT,
                paragraphs TEXT,
                char_count INTEGER DEFAULT 0,
                segment_count INTEGER DEFAULT 0,
                segments_meta TEXT DEFAULT '[]',
                FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE,
                UNIQUE(book_id, chapter_index)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_book_chapters_book ON book_chapters(book_id, chapter_index)")
        # ── 书籍批注表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS book_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                segment_index INTEGER NOT NULL,
                annotations TEXT DEFAULT '[]',
                summary TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL,
                FOREIGN KEY (book_id) REFERENCES books(book_id) ON DELETE CASCADE,
                UNIQUE(book_id, chapter_index, segment_index)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_book_annotations_ch ON book_annotations(book_id, chapter_index)")
        # ── 小剧场对话表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS theater_conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                persona_id TEXT,
                model TEXT NOT NULL DEFAULT 'gemini-3-flash',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_theater_conv_updated ON theater_conversations(updated_at DESC)")
        # ── 小剧场消息表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS theater_messages (
                id TEXT PRIMARY KEY,
                conv_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                attachments TEXT DEFAULT '[]',
                FOREIGN KEY (conv_id) REFERENCES theater_conversations(id) ON DELETE CASCADE
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_theater_msg_conv ON theater_messages(conv_id, created_at)")
        # ── 礼物表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gifts (
                id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                message TEXT NOT NULL,
                gift_type TEXT DEFAULT 'image',
                html_content TEXT DEFAULT '',
                created_at REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                received_at REAL
            )
        """)
        try:
            await db.execute("ALTER TABLE gifts ADD COLUMN gift_type TEXT DEFAULT 'image'")
        except:
            pass
        try:
            await db.execute("ALTER TABLE gifts ADD COLUMN html_content TEXT DEFAULT ''")
        except:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_status ON gifts(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_created ON gifts(created_at DESC)")
        # ── 基金持仓表 ──
        await db.execute("""
            CREATE TABLE IF NOT EXISTS fund_holdings (
                id TEXT PRIMARY KEY,
                fund_code TEXT NOT NULL,
                fund_name TEXT DEFAULT '',
                shares REAL DEFAULT 0,
                avg_cost REAL DEFAULT 0,
                total_cost REAL DEFAULT 0,
                warn_down REAL DEFAULT -3.0,
                warn_up REAL DEFAULT 15.0,
                created_at REAL NOT NULL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_fund_holdings_code ON fund_holdings(fund_code)")

        # worldbook 迁移与会话回填
        await _migrate_worldbooks_and_backfill(db)
        await db.commit()


def get_db():
    return aiosqlite.connect(DB_PATH)


async def _migrate_worldbooks_and_backfill(db):
    import time
    from config import load_worldbook, save_worldbook

    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT COUNT(*) AS c FROM worldbooks")
    row = await cur.fetchone()
    count = row["c"] if row else 0

    # 首次迁移：把旧 worldbook.json 转成首条默认世界书
    if count == 0:
        wb = load_worldbook()
        now = time.time()
        wb_id = f"wb_{int(now*1000)}"
        await db.execute(
            """
            INSERT INTO worldbooks
            (id, name, ai_name, user_name, ai_persona, user_persona, system_prompt, is_default, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                wb_id,
                "默认世界书",
                wb.get("ai_name", "AI"),
                wb.get("user_name", "你"),
                wb.get("ai_persona", ""),
                wb.get("user_persona", ""),
                wb.get("system_prompt", ""),
                1,
                now,
                now,
            ),
        )

    # 兜底保证至少有一个默认世界书
    cur = await db.execute("SELECT id FROM worldbooks WHERE is_default=1 ORDER BY updated_at DESC LIMIT 1")
    drow = await cur.fetchone()
    if drow:
        default_id = drow["id"]
    else:
        cur = await db.execute("SELECT id FROM worldbooks ORDER BY created_at ASC LIMIT 1")
        frow = await cur.fetchone()
        if not frow:
            return
        default_id = frow["id"]
        await db.execute("UPDATE worldbooks SET is_default=1 WHERE id=?", (default_id,))

    # 回填旧会话 worldbook_id
    await db.execute(
        "UPDATE conversations SET worldbook_id=? WHERE worldbook_id IS NULL OR worldbook_id=''",
        (default_id,)
    )

    # 同步默认世界书到旧 worldbook.json，兼容未改造调用点
    cur = await db.execute(
        """
        SELECT ai_name, user_name, ai_persona, user_persona, system_prompt
        FROM worldbooks WHERE id=?
        """,
        (default_id,),
    )
    wbrow = await cur.fetchone()
    if wbrow:
        save_worldbook({
            "ai_name": wbrow["ai_name"],
            "user_name": wbrow["user_name"],
            "ai_persona": wbrow["ai_persona"],
            "user_persona": wbrow["user_persona"],
            "system_prompt": wbrow["system_prompt"],
        })
