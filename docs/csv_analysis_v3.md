# CSV 数据分析 v3

## 目标

CSV 不再只是统计报告，而是先识别表型、推荐可用的图，再按单文件/多文件给出不同的图表策略，并把结果放进一个以产物为主的工作区。

## 处理链路

1. 创建：`POST /tasks/csv` 接收 multipart `files[]`（兼容单个 `file`），1–5 个 `.csv`，单个 ≤5 MB、总计 ≤20 MB。
2. 入沙箱：单文件写 `input.csv`；多文件写 `input-01-name.csv` 等扁平文件。
3. 分析：每个文件执行 `python analyze_csv.py <input> <summary>.json`，产出列统计、列角色、表型、推荐视图与图表数据。
4. 合并：单文件保留 v2 顶层字段并追加 `datasets[]`；多文件顶层 `table_kind=multi_dataset`、`recommended_view=profile_bar`，每个数据集保留完整 `view_options` 与全部可用图表。
5. 解读：仅单文件调用 LLM 写 150–220 字 `insight`，失败不阻断；多文件同样生成全部图表，对话继续做后续分析。
6. 展示：任务详情切换为产物优先布局，数据集 tab → 视图 tab；折线/柱状/饼图/热力/散点/思维导图由 ECharts 5.5（SVG renderer）渲染。

## 数据契约（summary.json）

```json
{
  "dataset_count": 2,
  "table_kind": "multi_dataset",
  "palette": ["#466F87", "#78B6C8", "#F2D98E", "#E48578", "#98AF1E"],
  "datasets": [
    {
      "name": "timeseries.csv",
      "rows": 4,
      "columns": 3,
      "table_kind": "numeric_timeseries",
      "recommended_view": "line",
      "profile": {},
      "view_options": [],
      "charts": {}
    }
  ]
}
```

单文件额外保留 `rows/columns/profile/table_kind/recommended_view/view_options/charts/insight` 作为顶层别名。

## 图表策略

- 可选视图：折线、柱状、饼图、热力、散点、思维导图、列总览。
- 单文件：按画像生成多张可用图，前端可自由切换。
- 多文件：每个数据集生成全部可用图表，保证切换数据集后不会缺少可视图。
- 硬阻断：少于 2 个点不出折线；无数值列不出折线/热力；无层级不出思维导图；单类别不出饼图。不可用视图灰显并给出原因。
- 配色固定为 `#466F87 #78B6C8 #F2D98E #E48578 #98AF1E`，由 summary 传给前端；折线、柱状、饼图、热力、散点、思维导图全部消费同一 palette。
- 图表基准宽度 960，图内字号 11.5–15；点击图表打开灯箱，可 50%–300% 缩放并下载 SVG/PNG。

## 前端布局

- 点击任务后统一进入 `body.detail-open` 三栏：`72px` 侧栏 + 自适应任务列表 + 约 3/4 屏产物区；宽屏为 `72px minmax(300px,1fr) minmax(0,3fr)`，≤1500 为 `72px 400px minmax(0,1fr)`。
- 列表在产物区打开时隐藏类型、步骤列，优先保证任务标题可读。
- `>1500px`：CSV 左侧分析区 + 320px 聊天；`≤1500px`：单列，聊天移到图表下方（340px）；`≤1080px`：产物区全宽浮层。
- 分析区顺序：摘要卡（表型/规模/完整度/重复行/推荐视图/解读）→ 数据集 tab → 视图 tab → 图表 → 任务产物/执行步骤折叠。
- 创建弹窗为 780px 单列，虚线上传区、已选文件卡、单文件/总大小限制提示。

## LLM 与后续生图

- 单文件：模板调用 LLM 写解读，失败静默跳过；聊天基于 `columns_detail`、`sample_rows`、报告回答。
- 多文件：聊天开场列出数据集名称、行数、表型与推荐视图；后续可按需扩展 `[[chart]]` 指令、`CSVChartService`、消息 metadata 与 `/tasks/{id}/charts`，当前版本尚未实现。

## 验收

- `tests/test_csv.py`：单文件表型/视图、对比清单思维导图、多文件 datasets 与 API。
- 全量：`python -m unittest discover -s tests -q`，当前 153 项通过（1 项真实沙箱测试默认跳过）。
- 浏览器：1280 宽多文件任务 `b19673d3` 验证 dataset 切换、SVG 图、3/4 产物区、灯箱缩放与下载；1764 宽产物区约 70%；创建弹窗 780×806。
