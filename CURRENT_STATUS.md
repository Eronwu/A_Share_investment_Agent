# CURRENT_STATUS.md

> 最后更新：2026-03-16
> 分支：`nightly`

## 当前结论

这个产品当前已经进入 **“可稳定使用，但东财板块成分股仍未完全 live 打通”** 的状态。

一句话说：

- **nightly 产品链路已可用**
- **板块池构建已可用**
- **当前采用：显式板块码 + 最近一次成功东财结果缓存**
- **尚未做到：每次都实时从东财稳定拉取板块成分股**

---

## 现在能做什么

### 1. 单票分析可用

常用命令：

```bash
./stock 159316
./stock 000661 --summary
./stock 600519 --show-reasoning --num-of-news 10
./stock 601985 --hq
```

适合：
- 单只股票 / ETF 分析
- 临时研究
- 手动查看推理和结论

### 2. nightly 批量扫描可用

推荐统一入口：

```bash
poetry run python tools/nightly_run.py --config config/nightly_run.v1.json
```

执行内容：
1. 建池：生成 `config/sector_pool.generated.json`
2. 批量执行 `./stock <ticker> --summary`
3. 排序并生成候选
4. 生成报告 / push summary
5. 发送 Telegram 摘要

### 3. 报告产物可用

输出目录：

```bash
reports/<timestamp>/
```

重点文件：
- `daily_report.md`
- `report.json`
- `final_candidates.json`
- `push_summary.txt`
- `push_summary.json`

---

## 当前板块池工作模式

当前并不是：

- 每次都从东财实时重建板块目录 + 成分股

而是：

1. `config/sector_pool_rules.v1.json` 中固化了目标板块 `board_code`
2. 建池时优先尝试东财 live 请求
3. 若 live 成分股抓取失败，则回退到：
   - `config/sector_pool.generated.seed.json`
   - 或已有的 `config/sector_pool.generated.json` 中最近一次成功结果
4. 因此最终生成池目前通常会是：
   - `generation_mode: cached`

### 当前实测状态

最近一次生成结果表现为：

- `catalog_used: false`
- 9 / 9 sector 进入 `cached`
- 每个 sector 都恢复了 `sample_rows`
- nightly smoke test 可正常跑通并生成报告

---

## 为什么不是 fully live

当前阻塞点已经缩小到一个非常具体的接口：

```text
push2.eastmoney.com/api/qt/clist/get?...fs=b:BKxxxx...
```

这个接口在当前环境下会稳定出现：

- `ERR_EMPTY_RESPONSE`
- `RemoteDisconnected`
- `Connection aborted`

而且已经确认：

- 不是简单的 requests 参数写错
- 不是只在 Python 脚本里失败
- 在真实浏览器页面上下文里，这个端点也会稳定失败
- 同页面的其他东财接口（例如 `kamt/get`、`stock/trends2/get`）仍可正常返回

这说明问题不是整个站点不可用，而是：

> **板块成分股这条 `clist/get` 接口在当前机器环境下被东财稳定掐断。**

---

## 目前最稳的使用方式

### 推荐：按 nightly 产品来用

```bash
poetry run python tools/nightly_run.py --config config/nightly_run.v1.json
```

这适合：
- 每晚自动扫描
- 产出候选池
- 生成日报
- 推送 Telegram 摘要

### 推荐：单票分析作为手动补充

如果想进一步看某个 ticker：

```bash
./stock 000988 --summary
./stock 300502 --show-reasoning
```

---

## 当前能力边界

### 已经可靠的

- 单票分析
- nightly 统一入口
- 报告生成
- Telegram 推送
- 板块规则固化
- 东财成功快照缓存复用

### 尚未完全可靠的

- 东财 live 板块成分股抓取
- 每次都实时刷新 constituent list
- 无人值守依赖东财 live constituent fetch 的全动态建池

---

## 后续建议

### 路线 1：继续深挖 live constituent 替代链路

优先研究：
- `BKxxxx.html` 页面内部真实网络请求
- 是否存在可替代 `clist/get` 的隐藏接口
- 是否可通过页面渲染完成后提取 constituent rows

### 路线 2：把当前稳定版正式产品化（推荐）

即采用：

- **目录 live**
- **摘要 live**
- **成分股 cached**
- **后台异步重试刷新快照**

这是当前最稳、最适合长期 nightly 运行的版本。

---

## 给未来自己的提醒

如果下次继续接手这个项目，优先记住：

1. 不要再把时间浪费在“是不是单纯代理问题”上 —— 已经证明不是。
2. 现在系统不是坏了，而是已经进入了 **稳定降级模式**。
3. 真正没打通的是 **板块成分股 live**，不是整个 nightly 产品。
4. 如果追求产品可用性，优先巩固 cached 方案，而不是死磕实时 constituent。
