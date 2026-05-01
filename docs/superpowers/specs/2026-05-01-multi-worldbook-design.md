# 多世界书与会话绑定设计

日期：2026-05-01  
状态：已与用户确认，可进入实施计划

## 1. 背景与目标
当前系统仅支持单一世界书（`data/worldbook.json`），所有会话共享同一份人设与系统提示。用户希望：
- 支持多个世界书；
- 新建聊天自动使用“全局默认世界书”；
- 聊天页可随时切换“当前会话”使用的世界书；
- 默认世界书管理放在 `/worldbook` 页面；
- 聊天页仅保留“世界书下拉”，不增加额外按钮。

## 2. 范围
### 2.1 包含
- 数据层：新增 worldbooks 数据结构与会话绑定关系；
- 迁移：旧单世界书自动迁移；
- API：世界书列表、CRUD、设默认、会话切换；
- 聊天页：语音监听上方增加世界书下拉；
- 世界书页：从单表单升级为列表+编辑。

### 2.2 不包含
- Android 端原生 UI 适配（本需求在 Web 页面层完成）；
- 世界书导入导出功能；
- 世界书共享/权限体系。

## 3. 数据与模型设计
### 3.1 新增表：`worldbooks`
字段建议：
- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `ai_name TEXT NOT NULL DEFAULT 'AI'`
- `user_name TEXT NOT NULL DEFAULT '你'`
- `ai_persona TEXT NOT NULL DEFAULT ''`
- `user_persona TEXT NOT NULL DEFAULT ''`
- `system_prompt TEXT NOT NULL DEFAULT ''`
- `is_default INTEGER NOT NULL DEFAULT 0`
- `created_at REAL NOT NULL`
- `updated_at REAL NOT NULL`

约束策略：
- 默认项唯一通过应用层保证（设置默认时先清零再置 1）。

### 3.2 conversations 扩展
- 新增字段：`worldbook_id TEXT`
- 语义：该会话固定绑定某个世界书。

## 4. 迁移与兼容
### 4.1 启动迁移流程
1. 若不存在 worldbooks 数据，则读取 `data/worldbook.json`；
2. 创建首条世界书（名称：`默认世界书`），内容来自旧 worldbook；
3. 设 `is_default=1`；
4. 批量将历史 conversations 的 `worldbook_id` 回填为该默认 ID（对空值记录生效）。

### 4.2 兼容读取策略
- 聊天请求有 `conv_id` 时：按会话 `worldbook_id` 读取；
- 无会话上下文任务（如部分后台任务）读取“默认世界书”；
- 对于绑定的 worldbook 被删情况：回退默认并记录一次系统日志。

## 5. API 设计
### 5.1 世界书接口
- `GET /api/worldbooks`：返回列表（含默认标记）
- `POST /api/worldbooks`：新建
- `PUT /api/worldbooks/{id}`：更新
- `DELETE /api/worldbooks/{id}`：删除（默认项禁止直接删）
- `PUT /api/worldbooks/{id}/default`：设为默认
- `GET /api/worldbooks/default`：获取默认世界书

### 5.2 会话接口扩展
- `POST /api/conversations`：
  - 入参可选 `worldbook_id`；
  - 不传则自动使用默认世界书。
- `PUT /api/conversations/{conv_id}/worldbook`：切换当前会话世界书。

## 6. 前端交互设计
### 6.1 聊天页（/chat）
- 位置：配置弹窗中，放在“语音监听”上方。
- 组件：仅一个“世界书下拉”。
- 行为：
  - 打开会话时展示当前绑定 worldbook；
  - 变更下拉即调用会话切换 API 并立即生效；
  - 不提供“设默认”与“管理入口”按钮。

### 6.2 世界书页（/worldbook）
- 升级为列表+编辑器结构：
  - 左/上：世界书列表（含默认标识与“设默认”按钮）；
  - 右/下：编辑表单（ai/user 名称与 persona/system_prompt）；
  - 支持新建、保存、删除；
  - 删除默认项时提示先切换默认。

## 7. 核心流程
### 7.1 新建会话
1. 前端发 `POST /api/conversations`（不传 worldbook_id）；
2. 后端读取默认 worldbook_id 并写入会话；
3. 返回会话对象（包含 worldbook_id）。

### 7.2 发送消息
1. 按 `conv_id` 找到会话 worldbook_id；
2. 用该 worldbook 构建 prompt 前缀；
3. 执行现有模型流式回复。

### 7.3 会话内切换世界书
1. 用户改下拉；
2. 前端调 `PUT /api/conversations/{id}/worldbook`；
3. 后续发送消息即走新 worldbook。

## 8. 错误处理
- worldbook_id 不存在：返回 400，并前端回退到会话当前值；
- 默认 worldbook 缺失：后端自动修复（取最早一条置默认），并记录日志；
- 删除被引用 worldbook：禁止删除或先迁移引用（本期采用禁止删除并提示）。

## 9. 测试与验收
### 9.1 功能验收
- 旧数据迁移后可正常聊天；
- 新建会话自动带默认世界书；
- 聊天页下拉切换只影响当前会话；
- 世界书页设置默认后，新建会话立即生效；
- 删除默认世界书被正确阻止。

### 9.2 回归点
- 监控/日程/位置等调用世界书的后台链路仍可运行；
- 语音发送、视频通话、再生成等聊天路径不受影响。

## 10. 实施建议（高层）
1. 先做 DB 迁移与后端读写路径；
2. 再做 worldbook 页面升级；
3. 最后接聊天页下拉与会话绑定；
4. 完成后执行端到端回归。

