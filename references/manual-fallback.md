# Manual Fallback

当自动抓取失败时，让用户只做这一件事：

1. 在浏览器打开目标页面。
2. 打开开发者工具 -> Network。
3. 刷新页面并筛选 `JS`。
4. 把核心JS（通常是体积最大的主 bundle + 可疑 chunk）保存到本地目录。
5. 把目录路径告诉 AI。

最短指令模板：
`请把主bundle和相关chunk保存到 <目录路径>，然后继续静态审计。`

AI 后续动作：

1. 自动读取该目录所有 `.js` 文件。
2. 优先按关键词定位：`sign` `token` `encrypt` `CryptoJS` `subtle`。
3. 若仍定位失败，切到纯静态审计，按 Source-Sink 链路回溯加密方法和密钥来源。
