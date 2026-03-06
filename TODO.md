# TODO

## Frontend

- [ ] `/sim` 页面交易计划区域：order 有 `indicators` 字段时，在 label 后追加彩色 tag 显示指标条件
  - 示例: `[RSI>30]` `[RSI<70]` `[MACD收窄3日]` `[金叉]` `[死叉]` `[量比>1.5]`
  - 文件: `web/app/sim/page.tsx`
  - 依赖: 后端已完成（`TradePlanEngine` 支持 `indicators` 字段）
