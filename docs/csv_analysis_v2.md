# CSV 数据分析 v2

## 目标

CSV 任务不再只是“行列统计 + 文本报告”，而是先理解表，再推荐合适的图，最后交给模型做可选解读和问答。

## 链路

1. `POST /tasks/csv` 接收文件（≤5 MB），`input.csv` 落到任务目录。
2. 沙箱执行 `python analyze_csv.py input.csv summary.json`。
3. `analyze_csv` 输出 `summary.json`：
   - 基础统计：行列数、重复行、缺失单元格、完整度；
   - `profile`：每列角色（time/measure/enum/category/long_text/hierarchy）；
   - `table_kind`：数值时序 / 二维交叉 / 数值快照 / 对比清单 / 纯文本归档 / 混合；
   - `view_options`：8 个视图的启用状态、分数与原因；
   - `charts`：仅生成可用视图的数据，带条目上限；
   - `sample`：前 10 行，供解读与聊天引用。
4. 模板可选调用 LLM 写 150–220 字解读，写入 `summary.insight` 与 `report.md`；失败不阻断任务。
5. 前端从 `summary.json` 取图表数据，在任务详情里原生 SVG 绘制，不引入图表库。

## 视图与硬阻断

| 视图 | 条件 |
| --- | --- |
| 折线图 | 时间/版本列 + 数值列，≥2 个点 |
| 柱状图 | 分类列 + 数值列，按均值聚合，最多 15 类 |
| 饼图 | 2–8 个非负分类值 |
| 热力图 | 两个维度 + 数值，或 标签 × 多指标 |
| 散点图 | 两个数值列，最多 200 点 |
| 思维导图 | 层级列，或 对比清单（标签 + 多方描述） |
| 列总览 | 永远可用 |

不具备条件的视图在界面上禁用并给出原因，而不是渲染空白图。

## 前端布局

- 宽屏（>1080）任务详情以 980px 覆盖层打开，左侧为分析区，右侧固定 320px 聊天。
- 分析区顺序：结论摘要卡（表型/规模/完整度/推荐视图/解读）→ 图表卡（视图切换 + SVG）→ 任务产物/执行步骤折叠。
- ≤1080 退化为单列，聊天在图表下方，高度 380px。
- 所有图表使用同一套 SVG 工具函数，坐标轴、图例、空状态与禁用态统一。

## 模型与降级

- 全局 `LLM_MODEL` 不可用时，`LLM_FALLBACK_MODEL`（默认 `qwen-plus`）会兜底一次；空输出也会触发兜底。
- `_insight` 提示词要求引用 `columns_detail` 的 min/max，`sample_rows` 只是前几行，避免用局部样本推断全表。
- CSV 聊天的开场白只作展示，`TaskChatService.ask` 不会把开场 assistant 消息送进模型上下文。

## 验证

```bash
python -m unittest tests.test_csv -q
python -m unittest discover -s tests -q
```

样例位于 `runtime/csv_samples/`：时序、对比清单、二维矩阵、纯文本。
