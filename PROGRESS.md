# PROGRESS.md

> 最后更新：2026-03-15
> 分支：`nightly`

## 当前目标

把 A 股项目的 nightly 自动化收口，并解决东方财富（东财）股票池构建在当前机器环境下不稳定的问题。

当前重点不是分析器本体，而是：

1. 夜跑配置与统一入口
2. 夜跑通知与调度
3. 东财原始接口构建股票池的稳定性

---

## 已完成

### 1) nightly 闭环的工程化基础

已完成并提交：

- `a2e356b` `feat: add unified nightly runner and push summary`
- `c6160e6` `feat: add nightly notify fallback and scheduler docs`
- `058b8bc` `fix: bypass proxies for eastmoney pool fetches`

具体包括：

- 统一 nightly 配置：`config/nightly_run.v1.json`
- 统一入口：`tools/nightly_run.py`
- push 摘要产物：`push_summary.txt` / `push_summary.json`
- Telegram 通知接入（nightly runner 侧）
- macOS 调度接入（launchd 脚本与说明）
- build_pool 失败时复用已有 generated pool 的兜底逻辑

### 2) 之前的股票池 / nightly 能力

之前已经有并验证过：

- 半自动建池
- nightly scan 主流程
- final candidates
- 多日稳定性统计
- retry / resume / skip-existing
- 候选池分层与报告产物

### 3) smoke test 结果

已验证过：

- nightly runner 能跑通一轮小规模 smoke test
- 能正常生成报告目录与 `push_summary`
- Telegram 通知链路本身可工作

---

## 东财问题：目前已确认的事实

这是当前最重要的部分。

### 已排除的方向

以下都不是根因的完整解释：

- 不是单纯的全局代理问题
- 不是 akshare 单独的问题
- 不是 patch 点没打到的问题
- 不是单纯 requests / curl 写法不对的问题

### 已确认的现象

#### 1. 普通脚本链路不稳定

在这台机器上，以下方式访问东财接口不稳定，常见报错：

- `Empty reply from server`
- `RemoteDisconnected`
- `Connection aborted`

受影响方式包括：

- `requests`
- `curl`
- akshare 封装
- 直接调用东财原始 `push2` 接口

#### 2. IPv6 有明显问题

已验证：

- IPv4 某些请求可以通
- IPv6 明显更容易被东财直接断开

因此脚本里已经尝试过强制 IPv4。

#### 3. 东财不是“完全不通”

这是目前最关键的结论：

在 **浏览器页面上下文** 中，东财自己的前端调用方式是能拿到数据的。

尤其是：

- 在东财页面里
- 用 `jQuery.ajax`
- `dataType: 'jsonp'`
- 调 `push2.eastmoney.com`

**可以拿到板块成分股返回。**

也就是说：

> 东财并不是彻底不可达，而是当前机器上“普通脚本请求链路”不被它稳定接受。

---

## 当前实验进展

### 成功验证过的

#### A. 成分股：可通过页面上下文 + JSONP 获取

已在东财页面上下文中成功拿到类似下面的真实数据：

- `603366 日出东方`
- `002543 万和电气`

这说明：

- 板块成分股数据本身可取
- 关键在于“访问方式”而不是“数据源不存在”

#### B. 行业目录：在某些浏览器上下文中可取

已出现过成功结果，例如：

- `BK1452 卫浴电器`
- `BK1432 氮肥`

#### C. 概念目录：在宿主浏览器上下文中可取

已出现过成功结果，例如：

- `BK1645 昨日打二板以上表现`
- `BK1644 微盘精选`

### 仍未稳定的

#### 1. headless 浏览器不稳定

测试表明：

- headless Playwright 经常 timeout
- 有头 Chrome/页面上下文更容易拿到结果

#### 2. 目录接口比成分股更挑上下文

目前看起来：

- 成分股接口更容易通过页面 JSONP 路径成功
- 行业/概念目录在脚本化环境里更容易 timeout

#### 3. 还没完成脚本级稳定收口

现在还没做到：

- `build_sector_pool.py` 稳定生成 `sector_pool.generated.json`
- 连续多次运行稳定成功
- 无人值守 nightly 可放心依赖这条东财链路

---

## 当前代码上的尝试

当前 `tools/build_sector_pool.py` 已做过这些方向的尝试：

1. 去掉对 akshare 板块抓取的依赖
2. 改成直接访问东财原始接口
3. 显式屏蔽代理环境
4. 强制 IPv4
5. 浏览器化 headers
6. 研究东财页面自身使用的接口与调用方式

但这部分还**未收口完成**，因此当前工作区中仍有未提交修改。

---

## 当前 blocker（当前阻塞点）

### 核心 blocker

**如何把“浏览器页面里能成功的 JSONP 请求方式”，稳定搬进项目脚本中。**

这件事难点不在“有没有接口”，而在：

- 页面上下文依赖
- headless / headed 行为差异
- 普通 fetch / requests 与 JSONP 的差异
- 不同东财子域对脚本请求的容忍度不同

### 更具体地说

现在已经知道：

- 宿主浏览器里某些请求能通
- Python + Playwright 里某些 JSONP 请求也能通（尤其成分股）
- 但还没把“行业目录 + 概念目录 + 成分股”三段都做成**稳定可重复**的脚本链路

---

## 下一步计划

### 路线 A（当前主路线）

继续把东财抓取改造成：

1. 优先走东财原始接口
2. 当普通 requests 失败时
3. 切换到 **Playwright + 页面上下文 + JSONP**
4. 成功后统一回填到现有池子生成逻辑

### 路线 B（如果目录仍不稳定）

如果“目录”仍然不稳定，会采用折中方案：

- 将规则中使用到的目标板块代码固化到配置里
- nightly 运行时只通过东财原始接口抓取**成分股**
- 不再每次都动态扫描全部板块目录

这条路线仍然属于“走东财原始数据”，只是减少对最不稳定那一层目录抓取的依赖。

---

## Definition of Done（完成标准）

只有同时满足下面这些，才算真的完成：

1. `build_sector_pool.py` 能在当前机器上稳定运行
2. 能生成新的 `config/sector_pool.generated.json`
3. 生成结果不是纯 fallback，而是实际来自东财数据
4. nightly runner 能复用这套结果正常夜跑
5. 代码已提交到当前分支

---

## 当前工作区状态（截至本文档更新时）

未提交修改主要集中在：

- `tools/build_sector_pool.py`
- `README_nightly_scan_v1.md`

说明：

- 这些修改仍属于“验证中的东财打通方案”
- 在没有跑通前，不应视为最终方案

---

## 给后续接手自己的备注

如果你下一次回来看这个项目，优先做这几件事：

1. 不要再回头纠结代理是否唯一根因——已经证明不是
2. 重点盯住：
   - 浏览器上下文
   - JSONP
   - headless / headed 差异
3. 优先验证“成分股接口”是否可稳定抽到
4. 如果目录仍不稳，优先考虑固化板块代码，不要死磕全目录动态扫描

---

## 一句话总结

nightly 工程化外壳已经基本成型；
真正没收口的，是 **“东财原始接口股票池构建在当前机器环境下的稳定打通”**。
