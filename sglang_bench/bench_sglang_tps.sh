#!/bin/bash
#===============================================================================
# SGLang 三指标快速测试：Prefill TPS / Decode TPS / Batch Size
# 用法:
#   bash bench_sglang_tps.sh
#
# 前置条件: SGLang 服务已运行，可通过 localhost:30000 访问
# 需要 --dataset-name random，离线环境请改为 random-ids
#===============================================================================

set -euo pipefail

API_URL="${1:-http://localhost:30000}"
MODEL="${2:-/mnt/data/models/gemma-4-12B-it}"
TOKENIZER="${3:-$MODEL}"

HOST="${API_URL#http://}"; HOST="${HOST%:*}"; HOST="${HOST%/}"
PORT="${API_URL##*:}"; PORT="${PORT%/}"

echo "============================================"
echo " SGLang 三指标快速测试"
echo " API: $API_URL"
echo " Model: $MODEL"
echo "============================================"
echo ""

# ─────────────────────────────────────────────
# 1. Prefill TPS
# ─────────────────────────────────────────────
echo "━━━ 1. Prefill TPS (input=1024, output=1) ━━━"
python3 -m sglang.bench_serving \
  --backend sglang-oai-chat \
  --host "$HOST" --port "$PORT" \
  --model "$MODEL" \
  --tokenizer "$TOKENIZER" \
  --dataset-name random \
  --random-input-len 1024 \
  --random-output-len 1 \
  --num-prompts 100 \
  --request-rate inf \
  --max-concurrency 1 \
  --apply-chat-template \
  --disable-tqdm 2>&1 | grep -E "Input token throughput|Total token throughput"

echo ""

# ─────────────────────────────────────────────
# 2. Decode TPS
# ─────────────────────────────────────────────
echo "━━━ 2. Decode TPS (input=1, output=1024) ━━━"
python3 -m sglang.bench_serving \
  --backend sglang-oai-chat \
  --host "$HOST" --port "$PORT" \
  --model "$MODEL" \
  --tokenizer "$TOKENIZER" \
  --dataset-name random \
  --random-input-len 1 \
  --random-output-len 1024 \
  --num-prompts 100 \
  --request-rate inf \
  --max-concurrency 1 \
  --apply-chat-template \
  --disable-tqdm 2>&1 | grep -E "Output token throughput|Total token throughput"

echo ""

# ─────────────────────────────────────────────
# 3. Batch Size 扫描
# ─────────────────────────────────────────────
echo "━━━ 3. Batch Size 扫描 (input=1024, output=128) ━━━"
for CONCUR in 1 4 8 16 32; do
  echo "--- max-concurrency=$CONCUR ---"
  python3 -m sglang.bench_serving \
    --backend sglang-oai-chat \
    --host "$HOST" --port "$PORT" \
    --model "$MODEL" \
    --tokenizer "$TOKENIZER" \
    --dataset-name random \
    --random-input-len 1024 --random-output-len 128 \
    --num-prompts 200 \
    --request-rate inf \
    --max-concurrency "$CONCUR" \
    --apply-chat-template \
    --disable-tqdm 2>&1 | grep -E "Output token throughput|Concurrency:|Peak concurrent"
  echo ""
done

echo "============================================"
echo " 测试完成"
echo "============================================"