# Gift HTML 模板库

目录：`aion-chat/templates/gift-html`

当前模板：
- `cute-envelope.html`（可爱信封）
- `glassmorphism-dynamic-card.html`（玻璃拟态动态卡片）
- `flip-blessing-card.html`（翻转祝福卡片）

使用建议：
- 这些文件作为“母版模板”存档，不直接当作业务接口返回文件。
- 需要发礼物时，优先复制一份再替换文案，避免改坏原模板。
- 若要支持动态注入，可约定占位符如：`{{title}}`、`{{content}}`、`{{signature}}`。

备注：
- 后续可继续追加新模板；如果场景不匹配，允许 AI 直接生成新 HTML。
