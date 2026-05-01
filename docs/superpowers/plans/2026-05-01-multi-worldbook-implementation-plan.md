# 实施计划：多世界书与会话绑定

关联设计：`docs/superpowers/specs/2026-05-01-multi-worldbook-design.md`
日期：2026-05-01

## 目标
实现多世界书、全局默认世界书、会话绑定世界书；聊天页提供会话级世界书下拉，世界书页负责默认项管理。

## 里程碑

## M1. 数据层与迁移
### 任务
1. 在 `aion-chat/database.py` 新增 `worldbooks` 表初始化 SQL。
2. 给 `conversations` 增加 `worldbook_id` 字段（幂等迁移）。
3. 在启动初始化中加入迁移流程：
   - 从 `data/worldbook.json` 导入首条“默认世界书”；
   - 回填旧会话的 `worldbook_id`。

### 涉及文件
- `aion-chat/database.py`
- `aion-chat/config.py`（补 worldbook 列表读写工具）
- `aion-chat/main.py`（确保启动时执行迁移）

### 验证
- 首次启动后 DB 存在 `worldbooks`；
- `conversations.worldbook_id` 存在并可读写；
- 旧数据可自动迁移成功。

---

## M2. 世界书后端 API
### 任务
1. 新增 `GET/POST/PUT/DELETE /api/worldbooks`（或分路径）。
2. 新增 `PUT /api/worldbooks/{id}/default`。
3. 新增 `GET /api/worldbooks/default`。
4. 保留兼容 `GET/PUT /api/worldbook`：
   - `GET` 返回默认世界书；
   - `PUT` 更新默认世界书内容（避免旧前端直接崩）。

### 涉及文件
- `aion-chat/routes/settings.py`
- `aion-chat/config.py`

### 验证
- 可以创建多个世界书；
- 默认项唯一；
- 删除默认项会被阻止并返回可读错误。

---

## M3. 会话绑定与聊天链路改造
### 任务
1. `POST /api/conversations` 支持 `worldbook_id` 可选入参；不传则绑定默认。
2. 新增 `PUT /api/conversations/{id}/worldbook`。
3. 将聊天发送/再生成/相关 prompt 注入点从全局 `load_worldbook()` 改为按会话读取。
4. 对无会话上下文的后台路径，回退默认世界书。

### 涉及文件
- `aion-chat/routes/chat.py`
- 可能涉及：`camera.py`、`schedule.py`、`location.py`（仅对读取策略做兼容封装）
- `aion-chat/config.py`（新增按会话 worldbook 获取函数）

### 验证
- 切换会话 worldbook 后，下一条回复立刻体现新设定；
- 不同会话可并行使用不同 worldbook；
- 旧会话正常。

---

## M4. 世界书页面升级（/worldbook）
### 任务
1. 把单页表单改为“列表 + 编辑器”。
2. 支持新建、删除、保存、设默认。
3. 默认项 UI 标识清晰；删除默认项时给出提示。

### 涉及文件
- `aion-chat/static/worldbook.html`
- `aion-chat/static/common.js`（如需复用 UI 工具）

### 验证
- 页面可管理多个世界书；
- 默认切换后新会话正确继承。

---

## M5. 聊天页配置弹窗接入
### 任务
1. 在语音监听上方增加“世界书下拉”。
2. 进入会话时加载并显示当前绑定项。
3. 切换下拉即调用会话绑定 API；仅保留下拉，不加额外按钮。

### 涉及文件
- `aion-chat/static/chat.html`

### 验证
- 新建会话默认显示默认 worldbook；
- 切换下拉只影响当前会话；
- UI 位置符合需求（语音监听上方）。

---

## M6. 回归与边界验证
### 回归范围
- 聊天发送、重生成、语音消息流程
- 摄像头/日程/定位触发链路
- 旧世界书接口兼容

### 核心边界
- 默认 worldbook 被删除尝试
- 会话绑定 worldbook 被删除
- 无默认 worldbook 的自修复逻辑

---

## 交付顺序建议
1. M1 + M2（先打稳数据与接口）
2. M3（保证链路正确）
3. M4 + M5（前端落地）
4. M6（回归收尾）

## 完成定义（DoD）
- 用户可创建/管理多个世界书；
- 可设置全局默认世界书；
- 新会话自动绑定默认；
- 聊天页可切换当前会话 worldbook；
- 关键链路回归通过，无旧功能回退。
