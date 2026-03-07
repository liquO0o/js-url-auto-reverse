# Workspace Venv Policy

仅在需要额外依赖时使用工作区临时虚拟环境。

## 原则

1. 禁止修改全局 Python/Node 环境。
2. 虚拟环境只能建在当前项目目录，例如 `.venv-js-reverse`。
3. 任务结束后可清理虚拟环境目录。

## 示例

```bash
python3 -m venv .venv-js-reverse
source .venv-js-reverse/bin/activate
pip install playwright
python scripts/dynamic_capture_playwright.py "https://example.com" --out ./runs/dyn
```

## 注意

- 动态模式是可选补充，不应替代静态证据链。
- 无需动态模式时，不创建虚拟环境。
