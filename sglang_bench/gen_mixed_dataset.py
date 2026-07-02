#!/usr/bin/env python3
"""生成混合长短 prompt 的自定义数据集"""

import json
import random

OUTPUT_FILE = "mixed_prompts.jsonl"
NUM_SAMPLES = 200  # 总条数

# ====== Prompt 模板（每个模板会被重复使用）======
PROMPT_TEMPLATES = [

    # --- 短 prompt (~5-10 tokens) ---
    "你好，请简单介绍一下你自己",
    "今天天气怎么样？",
    "1+1等于几？",
    "Hello, how are you?",

    # --- 中等 prompt (~50-100 tokens) ---
    "详细解释一下量子计算的原理，包括量子纠缠、量子门和量子纠错的具体机制",
    "请用 Python 实现一个快速排序算法，要求包含详细的注释和时间复杂度分析",
    "写一篇 500 字左右的短文，主题是人工智能对教育行业的影响",
    "Compare and contrast the economic policies of Keynesian economics and Monetarism, including their historical applications and modern relevance.",

    # --- 长 prompt (~200-500 tokens) ---
    "请根据以下法律条文分析案例：\n\n"
    "《中华人民共和国合同法》第一百零七条规定：当事人一方不履行合同义务或者履行合同义务不符合约定的，"
    "应当承担继续履行、采取补救措施或者赔偿损失等违约责任。\n\n"
    "第一百零八条规定：当事人一方明确表示或者以自己的行为表明不履行合同义务的，"
    "对方可以在履行期限届满之前要求其承担违约责任。\n\n"
    "第一百一十三条规定：当事人一方不履行合同义务或者履行合同义务不符合约定，"
    "给对方造成损失的，损失赔偿额应当相当于因违约所造成的损失，"
    "包括合同履行后可以获得的利益，但不得超过违反合同一方订立合同时预见到或者应当预见到的"
    "因违反合同可能造成的损失。\n\n"
    "案例事实：甲乙双方签订了一份买卖合同，约定甲方向乙方供应原材料，总价款 500 万元。"
    "合同签订后，原材料市场价格大幅上涨，甲方拒绝按原价供货，要求加价 30%。"
    "乙方不同意，诉至法院要求甲方继续履行合同并赔偿停工损失 80 万元。\n\n"
    "请结合以上法条，分析：(1)甲方是否构成违约？(2)乙方能否要求继续履行？"
    "(3)停工损失 80 万元是否属于可预见的损失？",

    # --- 超长 prompt (通过填充生成固定长度) ---
    None,  # 占位符，下面用 filler 填充
]

# 超长 prompt 模板（会在 ${FILLER} 处插入指定长度的填充文本）
EXTRA_LONG_TEMPLATE = (
    "请根据以下学术论文摘要进行分析：${FILLER}\n\n"
    "请从以下角度进行分析：\n"
    "1. 该研究的主要创新点是什么？\n"
    "2. 方法论是否存在局限性？\n"
    "3. 结论是否具有普遍适用性？\n"
    "4. 未来可能的改进方向？"
)


# ====== 填充文本生成 ======
def make_filler(num_chars, seed=42):
    """生成指定字符数的填充文本（内容一致但够长）"""
    rng = random.Random(seed)
    words = [
        "研究", "分析", "实验", "数据", "模型", "算法", "系统", "方法",
        "结果", "理论", "框架", "优化", "评估", "性能", "对比", "验证",
        "tokens", "benchmark", "throughput", "latency", "memory", "cache",
        "computation", "inference", "training", "quantization", "parallelism",
        "A survey of recent advances shows significant improvement in both",
        "The proposed architecture achieves state-of-the-art results across multiple",
        "Experimental results demonstrate that our method outperforms existing approaches",
        "We introduce a novel framework that effectively addresses the limitations of prior work",
    ]
    text = ""
    while len(text) < num_chars:
        text += rng.choice(words) + "，"
    return text[:num_chars]


def make_extra_long_prompt(target_chars=3000):
    """生成指定长度的超长 prompt"""
    filler = make_filler(target_chars)
    return EXTRA_LONG_TEMPLATE.replace("${FILLER}", filler)


# ====== 生成数据集 ======
with open(OUTPUT_FILE, "w") as f:
    for i in range(NUM_SAMPLES):
        # 按比例混合不同长度
        r = i / NUM_SAMPLES

        if r < 0.25:
            # 25% 短 prompt
            prompt = random.choice(PROMPT_TEMPLATES[:4])
        elif r < 0.55:
            # 30% 中等 prompt
            prompt = random.choice(PROMPT_TEMPLATES[4:6])
        elif r < 0.75:
            # 20% 长 prompt（法律案例）
            prompt = PROMPT_TEMPLATES[6]
        elif r < 0.90:
            # 15% 超长 prompt（~1000 chars）
            prompt = make_extra_long_prompt(1000)
        else:
            # 10% 极限长 prompt（~3000 chars）
            prompt = make_extra_long_prompt(3000)

        # 写入 OpenAI 格式（支持 --dataset-name openai）
        record = {
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 128,  # 每条输出 128 tokens
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

print(f"✅ 生成 {NUM_SAMPLES} 条混合长度 prompt → {OUTPUT_FILE}")
print(f"   短(25%) / 中(30%) / 长(20%) / 超长1000(15%) / 极限3000(10%)")
