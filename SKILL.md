---
name: js-url-auto-reverse
description: 输入网址后由AI自动拉取页面与JS到本地并执行逆向定位。支持 mode=static|dynamic|auto（默认 static）与 budget-level=low|medium|high（默认 low）。先做抓取、基础去混淆、关键词定位、静态Source-Sink审计，再做高级分析（反调试、加密原语识别、指纹链路、WASM线索）。动态阶段采用成本感知分级策略：先轻量 Hook，证据不足才升级到 CDP 断点暂停。自动流程失败时提示用户手动保存JS后继续静态审计定位加密方法或密钥来源。允许使用工作区内临时虚拟环境，禁止修改全局环境。
---

# JS URL Auto Reverse

## 目标

用户只提供 URL，后续抓取、落盘、定位、兜底由 AI 完成。

## 模式

1. `static`（默认）：仅执行自动抓取 + 纯静态审计。
2. `dynamic`：先静态，再动态补证据。
3. `auto`：先静态，只有在静态证据不足时才进入动态。

## 成本决策

1. 默认预算 `budget-level=low`，优先最省成本路径。
2. 动态流程采用阶梯策略：`静态 -> Hook 捕获 -> (必要时) CDP 断点暂停`。
3. 仅当满足以下条件之一才升级到 CDP：
- 用户强制 `--force-cdp`。
- Hook 阶段失败。
- Hook 证据分低于阈值（按预算档位控制）。
4. 默认输出压缩摘要；仅在排障时使用 `--verbose-output` 返回全量阶段数据。

## 覆盖边界

1. 优先覆盖常见前端签名/加密站点（CryptoJS/WebCrypto/轻中度混淆/动态chunk）。
2. 对强对抗站点（重WASM虚拟机、服务端协同签名、强风控）不做绝对成功保证。

## 执行原则

1. 不修改全局环境，不做全局安装。
2. 只在当前工作区写入 `runs/` 临时目录。
3. 若需要额外依赖，只允许工作区临时虚拟环境。
4. 默认禁止浏览器自动化，只有模式和条件满足时才启用。

## 标准流程

1. 自动抓取资源。
运行 `scripts/fetch_js_from_url.py`：
- 拉取 HTML。
- 拉取外链 JS。
- 保存内联 script 为本地 `.js`。
- 发现并尝试抓取静态 dynamic import URL。
- 发现并抓取 Worker 与 ServiceWorker 脚本。
- 发现并抓取 `.wasm` 资源。
- 抓取 `sourceMappingURL` 对应 sourcemap（如可访问）。

2. 基础去混淆。
运行 `scripts/deobfuscate_basic.py`：
- 删除 `debugger`。
- 简化常见布尔混淆 token。
- 标准化 `obj['k'] -> obj.k`。

3. 自动定位候选文件（显式定位步骤）。
运行 `scripts/locate_js_candidates.py`：
- 基于关键词与结构特征打分排序。
- 输出高优先级文件与命中原因（网络、加密、sign/token/key、混淆特征）。

4. 自动静态审计。
运行 `scripts/static_source_sink_audit.py`：
- 扫描 Source/Sink/加密关键词。
- 输出候选文件排序与置信度（`confirmed/likely/unknown`）。

5. 高级静态分析。
运行 `scripts/advanced_reverse_analysis.py`：
- 反调试信号识别。
- 加密原语与密钥来源识别。
- 指纹参数链路信号识别。
- WASM 二进制线索提取。

6. 可控动态模式（按模式触发，且受预算约束）。
先执行轻量 Hook：
- 抓取运行时动态加载 JS。
- 捕获 Hook 日志（`fetch/xhr/CryptoJS/subtle`）和控制台关键事件。

必要时升级 CDP：
- 使用 URL/XHR 断点暂停，捕获暂停点调用栈快照。
- 自动恢复执行，避免长时间阻塞。

全程限流：
- 事件数量、值长度、时间窗均有上限，控制成本与输出体积。

参考 `references/dynamic-mode-guidelines.md` 与 `references/hook-templates.js`。

7. 输出结论。
输出加密方法、参数拼接、密钥或密钥来源，并区分“已证实/推断”。

## 兜底策略

1. 自动抓取失败：
要求用户手动保存主 bundle 与相关 chunk 到本地目录，然后 AI 继续静态审计。

2. 手动保存后仍失败：
强制走纯静态 Source-Sink 链路，定位加密方法或密钥来源，明确未决项。

## 脚本入口

1. `scripts/fetch_js_from_url.py`
2. `scripts/deobfuscate_basic.py`
3. `scripts/locate_js_candidates.py`
4. `scripts/static_source_sink_audit.py`
5. `scripts/advanced_reverse_analysis.py`
6. `scripts/dynamic_capture_playwright.py`
7. `scripts/replay_validator.py`
8. `scripts/run_js_reverse_pipeline.py`
9. `scripts/run_regression_tests.py`

## 参考资源

1. `references/manual-fallback.md`
2. `references/dynamic-mode-guidelines.md`
3. `references/hook-templates.js`
4. `references/static-audit-checklist.md`
5. `references/replay-validator-guide.md`
6. `references/workspace-venv-policy.md`

## 回归测试

运行 `scripts/run_regression_tests.py`，覆盖三类样例：

1. 常规请求签名样例。
2. dynamic import 样例。
3. 轻混淆样例。

## 禁止事项

1. 不做未授权目标分析。
2. 不做破坏性操作。
3. 不做与任务无关的系统改动。
