# 创建任务板块重写与调研工作流执行文档

更新时间：2026-11-28

## 1. 目标

- 把「创建任务」从按类型拼字段的散装表单，改成三段式、可分组、可扩展的弹窗。
- 同一套外壳承载网页整理、网页对比、CSV、仓库体检、网页验收。
- 为后续调研能力（快照、标签、批注、结构化字段、导出）留出稳定落点。

## 2. 弹窗结构

```
┌ header 64px ─────────────────────────────┐
│ eyebrow / 标题                     关闭 × │
├ body 滚动 ───────────────────────────────┤
│ 模式切换（单页整理 / 多页对比）           │
│ 01 网页地址                               │
│ 02 批量订阅（默认收起）                   │
│ 03 批次                                   │
│ 04 阅读与抽取                             │
│ 标签                                      │
├ footer 72px sticky ──────────────────────┤
│                        取消   提交任务     │
└──────────────────────────────────────────┘
```

尺寸：`width: min(880px, 94vw)`、`height: min(760px, 90vh)`；移动端 `100dvh`，body 内部滚动，footer 始终可见。

## 3. 状态与渲染

- 模块级 `digestMode`（single / compare）与 `createType`。
- `renderCreateFields(type)` 负责整体重绘：先渲染类型选择（非功能页），再按类型渲染分组；网页整理额外渲染两个 `mode-pane`，切换只改 `hidden`，不销毁输入。
- `submitCreate` 仍从 DOM 读取，保持接口不变；标签在创建成功后 `PUT /tasks/{id}/tags`，批量创建对每个新任务应用。
- 字段编辑器：`addFieldRow / readFieldRows / saveFieldTemplate / applyFieldTemplate`，名称去重、最多 12 行，模板存 localStorage。

## 4. 字段矩阵

| 类型 | 分组 |
| --- | --- |
| 网页整理·单页 | 01 地址；02 批量（折叠）；03 批次；04 阅读目标 + 抽取方式 + 字段；标签 |
| 网页整理·多页 | 01 对比链接；02 预设维度；03 补充维度；标签 |
| CSV | 01 上传文件 |
| 仓库体检 | 01 仓库地址 |
| 网页验收 | 01 目标页面；02 验收条件 |

提交载荷：

- 单页：`{url, goal?, fields?, batch_targets?, batch_name?}`；
- 对比：`{urls[], dimensions[]}`；
- 标签：创建后 `PUT /tasks/{id}/tags {names[]}`。

## 5. 调研工作流（后续落点）

- 快照：`GET /tasks/{id}/snapshots` 返回 `{items:[{name,size,url}], message?}`，详情新增“网页快照”tab。
- 标签：`/tags` 与任务/文档标签接口已就绪，列表支持 `?tag=` 过滤。
- 批注：`annotations` 表与 CRUD 已就绪，等待“原文预览”tab 与划选交互。
- 导出：`GET /tasks/{id}/export?format=csv|md|gb7714` 已可用，待接入详情右上角下拉。

## 6. 数据迁移

`user_version 9 → 10`：新增 `tags`、`task_tags`、`kb_document_tags`、`annotations` 及索引；旧库自动迁移，失败回滚到升级前版本号。

## 7. 验收

- 五种类型都能打开弹窗并提交；单页/多页切换不丢输入、不跳高度。
- 字段模式下生成 `fields.csv`/`fields.json`，报告优先表格。
- 标签创建后出现在任务详情与列表；批量创建逐个继承。
- 快照、导出、批注接口返回 200（无产物时给中文提示）。
- 全量测试通过（当前 136 项，1 跳过）。
