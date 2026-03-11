# Nightly Scan v1

这是基于现有 `./stock` 单票分析入口之上的外挂式夜间批处理层，默认仍走当前项目主流程，不改单票分析行为。

## 现在能做什么

- 从 `config/sector_pool.v1.json` 读取板块股票池
- 对全部样本串行执行 `./stock <ticker> --summary`
- 为每只股票同时落地原始终端输出、summary 文本、summary JSON
- 按结构化 summary 优先排序；缺失时回退到终端文本解析
- 选 TopN 再跑 `--show-reasoning`
- 生成 `daily_report.md` 和 `report.json`

## 常用命令

```bash
# 先做一个很小的 smoke test
python tools/nightly_scan.py --limit 1 --skip-reasoning --num-of-news 3

# 跑 summary + TopN reasoning
python tools/nightly_scan.py --top-n 5

# 指定模型或高质量模式
python tools/nightly_scan.py --top-n 3 --model qwen2.5-coder:7b
python tools/nightly_scan.py --top-n 3 --hq
```

## 输出结构

- `reports/<timestamp>/daily_report.md`：人读报告
- `reports/<timestamp>/report.json`：汇总 JSON
- `reports/<timestamp>/raw/summary/*.txt`：每只 summary 原始终端输出
- `reports/<timestamp>/raw/summary/*.summary.txt`：每只 summary 渲染文本
- `reports/<timestamp>/raw/summary/*.summary.json`：每只 summary 结构化结果
- `reports/<timestamp>/raw/reasoning/*`：TopN reasoning 阶段对应产物

## 关键实现点

- `tools/nightly_scan.py` 优先读取 `./stock` 透传生成的 `--summary-json-out`
- `src/main.py` 新增可选 `--summary-json-out` / `--summary-text-out`，默认不影响原有单票使用方式
- 排序字段优先使用 `composite_score`，否则回退到 action / confidence / signal 的启发式组合

## 已知限制

1. 仍是 v1，默认串行执行，重点是稳定而不是吞吐。
2. `composite_score` 是 nightly 排序用启发式分数，不是策略回测收益分。
3. 若底层 agent 输出异常或 summary JSON 缺失，仍会退回文本解析，稳健性不如全链路原生 JSON。
4. 未做 retry / resume / 增量跳过。

## 后续建议

- 增加 resume / retry / failure cache
- 增加昨日报告 diff
- 若要提速，再引入受控并发而不是先改主工作流
