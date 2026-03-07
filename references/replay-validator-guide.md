# Replay Validator Guide

用于验证逆向还原后的签名/加密算法是否可重放。

## 1. 准备适配器

创建 `adapter.py`，实现：

```python
def generate(payload: dict) -> str:
    # 返回签名或密文字符串
    ...
```

## 2. 准备向量

`vectors.json`：

```json
[
  {"payload": {"id": 1, "ts": 1700000000}, "expected": "abc"},
  {"payload": {"id": 2, "ts": 1700000010}, "expected": "def"}
]
```

## 3. 执行验证

```bash
python3 scripts/replay_validator.py --adapter ./adapter.py --vectors ./vectors.json
```

## 4. 结果解释

- `pass_rate=1.0`：样本范围内可重放通过。
- `pass_rate<1.0`：优先检查时间戳、随机数、编码顺序、字段顺序。
