# Futu OpenAPI Rules

## request_history_kline 参数陷阱

**必须同时传 start 和 end**。即使官方文档说 `start=None, end=None` 默认取最近 365 天，实测 SDK 9.06.5608 下 `end=today + max_count=120` 返回的是上市初期数据，不是最近的。

```python
# ✗ 错误 — 返回上市起始的前 N 条，不是最近 N 条
ctx.request_history_kline(code, autype=QFQ, max_count=120)
ctx.request_history_kline(code, autype=QFQ, end=today, max_count=120)

# ✓ 正确 — 明确指定 start 和 end
import datetime
today = datetime.date.today()
start = (today - datetime.timedelta(days=200)).strftime("%Y-%m-%d")
end = today.strftime("%Y-%m-%d")
ctx.request_history_kline(code, autype=QFQ, start=start, end=end, max_count=200)
```

200 日历天 ≈ 135 个交易日，确保 >= 120 bars 用于 MA60 计算。

## 接口限制

| 限制 | 值 |
|------|---|
| 请求频率 | 每 30 秒最多 60 次 |
| 分 K 数据范围 | 最近 8 年 |
| 日 K 数据范围 | 最近 20 年 |
| 日 K 以上 | 无限制 |
| 单次最大返回 | 1000 根 (超过需分页) |
| 历史 K 线额度 | 30 天内消耗后释放 |
| 换手率 | 仅日 K 及以上 |

分页：首页传 `page_req_key=None`，后续传上次返回的 key。后续页不受频率限制。

## 行情权限

| 市场 | 免费 | 付费 |
|------|------|------|
| 港股 (中国内地 IP) | LV2 免费 | - |
| 港股 (港澳台/海外 IP) | LV1 免费 | 需购买 LV2 高级行情 |
| A 股 (中国内地 IP) | LV1 免费 | - |
| 美股 | 无权限 | 购买 Nasdaq Basic / TotalView |

## 订阅额度

根据账户资产/交易量分级：

| 条件 | 额度 |
|------|------|
| 开户用户 | 100 |
| 总资产 >= 1 万 HKD | 300 |
| 总资产 >= 50 万 HKD 或 月交易 > 200 笔 | 1000 |
| 总资产 >= 500 万 HKD 或 月交易 > 2000 笔 | 2000 |

同一股票不同 K 线周期只占 1 个额度。

## 复权类型

| AuType | 含义 | 用途 |
|--------|------|------|
| QFQ | 前复权 | 技术指标计算（连续价格序列） |
| NONE | 不复权 | 真实交易价格对比 |
| HFQ | 后复权 | 长期收益率计算 |

**注意**: QFQ 价格可能与实时价格差距大（如有配股/拆股历史）。用于评分的 kline close 应与实时价格做 sanity check，偏差 > 50% 时应 fallback。

## 数据质量注意事项

- 小票（低市值/低流动性）的 kline 数据可能不完整或延迟
- 连接断开后需重新订阅，不会自动恢复
- OpenD 需要保持运行，断开后 daemon 应能重连
- `request_history_kline` 返回 3 个值 `(ret, data, page_req_key)`，不是 2 个
