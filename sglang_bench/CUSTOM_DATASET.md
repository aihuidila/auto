# SGLang 自定义数据集模板

使用 `--dataset-name custom` 或 `--dataset-name openai` 加载本地 JSONL 文件。

---

## 格式一：ShareGPT 格式（`--dataset-name custom`）

```jsonl
{"conversations": [{"content": "What is the capital of France?", "role": "user"}, {"content": "The capital of France is Paris.", "role": "assistant"}]}
{"conversations": [{"content": "Explain quantum computing in simple terms.", "role": "user"}, {"content": "Quantum computing uses qubits...", "role": "assistant"}]}
{"conversations": [{"content": "Write a Python function to sort a list.", "role": "user"}, {"content": "Here's a quicksort implementation:\n\ndef quicksort(arr):\n    if len(arr) <= 1: return arr\n    pivot = arr[len(arr)//2]\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + middle + quicksort(right)", "role": "assistant"}]}
```

**字段说明**：
- `conversations`（或 `conversation`）— 对话轮次数组
- 每轮有 `content`（或 `value`）— 对话文本
- `role` — `"user"` 或 `"assistant"`

**使用命令**：
```bash
python3 -m sglang.bench_serving \
  --backend sglang-oai-chat \
  --dataset-name custom \
  --dataset-path ./my_custom_data.jsonl \
  --num-prompts 100 \
  --request-rate 10 \
  --max-concurrency 8 \
  --apply-chat-template
```

**参数控制**：
| 参数 | 作用 |
|------|------|
| `--sharegpt-output-len N` | 固定输出长度（覆盖数据集中的 assistant 实际长度） |
| `--sharegpt-context-len N` | 截断超长上下文（prompt + output > N 的丢弃） |
| `--prompt-suffix "..."` | 在所有 prompt 后追加文本 |
| `--apply-chat-template` | 应用 tokenizer 的 chat template 包装 |

---

## 格式二：OpenAI 格式（`--dataset-name openai`）

```jsonl
{"messages": [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": "What is 2+2?"}], "max_tokens": 128}
{"messages": [{"role": "user", "content": "Write a poem about AI."}], "max_tokens": 256, "temperature": 0.8}
{"messages": [{"role": "user", "content": "Summarize this article: ..."}], "max_tokens": 512, "tools": [{"type": "function", "function": {"name": "search", "description": "..."}}]}
```

**字段说明**：
- `messages` — OpenAI 标准消息数组
- `max_tokens`（可选）— 每条请求的输出长度（不设则用 `--sharegpt-output-len`）
- `temperature`, `top_p` 等 — 自动透传到 extra_request_body
- `tools` — 可选工具定义，token 计数自动计入 input

**使用命令**：
```bash
python3 -m sglang.bench_serving \
  --backend sglang-oai-chat \
  --dataset-name openai \
  --dataset-path ./my_openai_data.jsonl \
  --num-prompts 100 \
  --request-rate 10 \
  --max-concurrency 8
```

> 注意：`openai` 格式**不需要** `--apply-chat-template`，因为 messages 已经是结构化格式。

---

## 快速生成模板脚本

```python
#!/usr/bin/env python3
"""生成自定义测试数据集的模板脚本"""

import json

# ====== 配置 ======
OUTPUT_FILE = "bench_data.jsonl"
NUM_SAMPLES = 100

# ====== 模板数据 ======
# 你可以在这里写自己的 prompt/response 对
PROMPTS = [
    "What is machine learning?",
    "Explain the concept of overfitting.",
    "How does a transformer model work?",
    "Write a Python function for binary search.",
    "What is the difference between supervised and unsupervised learning?",
]

RESPONSES = [
    "Machine learning is a subset of artificial intelligence...",
    "Overfitting occurs when a model learns the training data too well...",
    "Transformer models use self-attention mechanisms to process sequences...",
    "Here's a binary search implementation:\n\ndef binary_search(arr, x):\n    low, high = 0, len(arr) - 1\n    while low <= high:\n        mid = (low + high) // 2\n        if arr[mid] == x: return mid\n        elif arr[mid] < x: low = mid + 1\n        else: high = mid - 1\n    return -1",
    "Supervised learning uses labeled data, while unsupervised learning finds patterns in unlabeled data.",
]

# ====== 生成 ======
with open(OUTPUT_FILE, "w") as f:
    for i in range(NUM_SAMPLES):
        idx = i % len(PROMPTS)
        record = {
            "conversations": [
                {"content": PROMPTS[idx], "role": "user"},
                {"content": RESPONSES[idx], "role": "assistant"},
            ]
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

print(f"✅ 生成 {NUM_SAMPLES} 条数据 → {OUTPUT_FILE}")
```

运行后得到 `bench_data.jsonl`，然后：

```bash
python3 -m sglang.bench_serving \
  --backend sglang-oai-chat \
  --dataset-name custom \
  --dataset-path ./bench_data.jsonl \
  --num-prompts 50 \
  --request-rate 10 \
  --max-concurrency 8 \
  --apply-chat-template
```

---

## 三种格式对比

| 维度 | `custom` (ShareGPT) | `openai` | `random` / `random-ids` |
|------|:-------------------:|:--------:|:-----------------------:|
| 数据来源 | 本地 JSONL | 本地 JSONL | 随机生成 |
| 内容 | 真实文本对话 | 真实消息 + 参数 | 随机 token / 随机 ID |
| 输出长度控制 | `--sharegpt-output-len` | 每行 `max_tokens` 或 `--sharegpt-output-len` | `--random-output-len` |
| 上下文长度过滤 | `--sharegpt-context-len` | 无 | 无 |
| Chat template | `--apply-chat-template` | 自动应用 | `--apply-chat-template` |
| 额外参数 | 无 | tools, temperature, top_p 等 | 无 |
| 适用场景 | 真实对话负载测试 | OpenAI API 兼容测试 | 基准性能标定 |