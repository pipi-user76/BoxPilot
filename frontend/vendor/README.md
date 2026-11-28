# Vendored assets

- `echarts.min.js`: ECharts 5.5.1, Apache-2.0, downloaded from `https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js`.
- `ECHARTS-LICENSE.txt`: upstream Apache-2.0 license.

Charts are initialized with `renderer: "svg"`. The word cloud view keeps the local SVG layout in `task_list.js` because the ECharts word-cloud extension is a separate canvas-only plugin.
