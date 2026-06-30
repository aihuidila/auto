#!/bin/bash
#===============================================================================
# SGLang bench_serving 自动化测试脚本
# 测试不同输入长度 × 不同并发数，输出完整性能指标
#
# 前置条件: 提前下载 ShareGPT 到 HF 缓存目录（random 数据集底层依赖 ShareGPT）
#   有网时跑一次即可：
#     export HF_ENDPOINT=https://hf-mirror.com
#     HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
#     mkdir -p "$HF_HOME/hub/datasets--anon8231489123--ShareGPT_Vicuna_unfiltered/snapshots/main/"
#     wget https://hf-mirror.com/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json \
#       -O "$HF_HOME/hub/datasets--anon8231489123--ShareGPT_Vicuna_unfiltered/snapshots/main/ShareGPT_V3_unfiltered_cleaned_split.json"
#   之后 random 数据集就会直接从缓存读取，不走网络。
#
# 用法:
#   bash sglang_bench_serving.sh <api_url> <tokenizer_path> [output_dir]
#
# 示例:
#   bash sglang_bench_serving.sh http://localhost:30006 \
#     /mnt/data/models/gemma-4-12B-it \
#     ./results
#
# 输出指标说明:
#   TTFT      - Time To First Token，首 token 延迟 (ms)
#   TPOT      - Time Per Output Token，每输出 token 延迟 (ms)
#   ITL       - Inter-Token Latency，token 间延迟 (ms)
#   E2E_Lat   - End-to-End Latency，端到端总延迟 (ms)
#   token/s   - 每秒输出 token 数
#===============================================================================

set -euo pipefail

API_URL="${1:?错误: 用法: $0 <api_url> <tokenizer_path> [output_dir]}"
TOKENIZER="${2:?错误: 请指定 tokenizer 路径}"
OUTPUT_DIR="${3:-./bench_results}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="${OUTPUT_DIR}/${TIMESTAMP}"
mkdir -p "$RESULTS_DIR"

# ===== 测试参数配置 =====
# 精确输入长度 (tokens) — random 数据集每条请求都精确生成这个长度
INPUT_LENS=(128 512 1024 2048 4096)
# 并发数
CONCURRENCIES=(1 2 4 8 16 32)
# 每个测试的请求数
NUM_PROMPTS=200
# 输出长度 (tokens)
OUTPUT_LEN=128
# warmup 请求数
WARMUP=20

# ===== 颜色 =====
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }

# ===== 检查 =====
log "API:          $API_URL"
log "Tokenizer:    $TOKENIZER"
log "输出目录:     $RESULTS_DIR"
log ""

curl -sf "$API_URL/health" > /dev/null 2>&1 || { echo "服务不可用: $API_URL"; exit 1; }
MODEL=$(curl -sf "$API_URL/v1/models" 2>/dev/null | python3 -c \
    "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "unknown")
log "模型: $MODEL"
log ""

API_HOST="${API_URL#http://}"; API_HOST="${API_HOST#https://}"
API_HOST="${API_HOST%/v1}"; API_HOST="${API_HOST%/}"
HOST="${API_HOST%%:*}"; PORT="${API_HOST##*:}"

# ===== CSV 结果文件 =====
CSV_FILE="$RESULTS_DIR/all_results.csv"
echo "input_len,concurrency,num_prompts,TTFT,TTFT_p50,TTFT_p99,TPOT,TPOT_p50,TPOT_p99,ITL,ITL_p50,ITL_p99,E2E_Latency,E2E_p50,E2E_p99,token_s" > "$CSV_FILE"

SUMMARY="$RESULTS_DIR/summary.txt"
{
    echo "============================================"
    echo " SGLang bench_serving 性能测试报告"
    echo "============================================"
    echo "API:       $API_URL"
    echo "模型:      $MODEL"
    echo "Tokenizer: $TOKENIZER"
    echo "时间:      $(date)"
    echo ""
    echo "测试配置:"
    echo "  输入长度:  ${INPUT_LENS[*]}"
    echo "  并发数:    ${CONCURRENCIES[*]}"
    echo "  请求数/组: $NUM_PROMPTS"
    echo "  输出长度:  $OUTPUT_LEN"
    echo "============================================"
    echo ""
    echo "in_len  concur  reqs  TTFT    p50    p99   TPOT    p50    p99    ITL    p50    p99  E2E_Lat  p50    p99  tok/s"
    echo "------ ------ ---- ------ ------ ------ ------ ------ ------ ------ ------ ------ -------- ------ ------ ------"
} > "$SUMMARY"

# ===== 主测试循环 =====
TOTAL=$(( ${#INPUT_LENS[@]} * ${#CONCURRENCIES[@]} ))
COUNT=0

for IN_LEN in "${INPUT_LENS[@]}"; do
    for CONCUR in "${CONCURRENCIES[@]}"; do
        COUNT=$((COUNT + 1))
        log "[$COUNT/$TOTAL] input_len=${IN_LEN}  concurrency=${CONCUR}"

        OUTPUT_FILE="$RESULTS_DIR/result_in${IN_LEN}_con${CONCUR}.jsonl"

        python3 -m sglang.bench_serving \
            --backend sglang-native \
            --host "$HOST" \
            --port "$PORT" \
            --model "$MODEL" \
            --tokenizer "$TOKENIZER" \
            --dataset-name random \
            --random-input-len "$IN_LEN" \
            --random-output-len "$OUTPUT_LEN" \
            --num-prompts "$NUM_PROMPTS" \
            --request-rate inf \
            --max-concurrency "$CONCUR" \
            --output-file "$OUTPUT_FILE" \
            --warmup-requests "$WARMUP" \
            --disable-tqdm \
            --disable-stream \
            --output-details \
            2>&1

        # 从 JSONL 文件解析指标
        python3 -c "
import json, sys

try:
    with open('$OUTPUT_FILE') as f:
        lines = f.read().strip()
except:
    lines = ''

records = []
if lines:
    for line in lines.split('\n'):
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except:
                pass

if records:
    ttfts = [r.get('ttft', 0) * 1000 for r in records if r.get('ttft') is not None]
    tpots = [r.get('tpot', 0) * 1000 for r in records if r.get('tpot') is not None]
    itls  = [r.get('itl', 0) * 1000 for r in records if r.get('itl') is not None]
    e2es  = [r.get('e2e_latency', 0) * 1000 for r in records if r.get('e2e_latency') is not None]
    output_tokens = [r.get('output_tokens', 0) for r in records if r.get('output_tokens') is not None]
else:
    ttfts = tpots = itls = e2es = output_tokens = []

def stats(arr):
    if not arr:
        return [0]*5
    s = sorted(arr)
    n = len(s)
    return [round(sum(s)/n, 2) if n>0 else 0,
            round(s[n//2], 2) if n>0 else 0,
            round(s[int(n*0.99)], 2) if n>0 else 0,
            round(s[0], 2) if n>0 else 0,
            round(s[-1], 2) if n>0 else 0]

ttft_s = stats(ttfts)
tpot_s = stats(tpots)
itl_s  = stats(itls)
e2e_s  = stats(e2es)
total_tokens = sum(output_tokens) if output_tokens else 0

print(f'$IN_LEN,\$CONCUR,{len(records)},{ttft_s[0]},{ttft_s[1]},{ttft_s[2]},{tpot_s[0]},{tpot_s[1]},{tpot_s[2]},{itl_s[0]},{itl_s[1]},{itl_s[2]},{e2e_s[0]},{e2e_s[1]},{e2e_s[2]},{total_tokens/(e2e_s[0]/1000) if e2e_s[0]>0 else 0}')
" 2>/dev/null | tee -a "$CSV_FILE"

    done
done

# ===== 汇总表格 =====
echo ""
echo "================================================"
echo " 结果汇总"
echo "================================================"

python3 -c "
import csv, sys

with open('$CSV_FILE') as f:
    rows = list(csv.DictReader(f))

if not rows:
    print('无数据')
    sys.exit(0)

rows.sort(key=lambda r: (int(r['input_len']), int(r['concurrency'])))

header = f\"{'in_len':>6} {'concur':>6} {'reqs':>5} {'TTFT':>7} {'p50':>7} {'p99':>7}  {'TPOT':>7} {'p50':>7} {'p99':>7}  {'ITL':>7} {'p50':>7} {'p99':>7}  {'E2E_Lat':>9} {'p50':>9} {'p99':>9}  {'tok/s':>8}\"
print(header)
print('-' * len(header))

for r in rows:
    in_len = int(r['input_len'])
    conc   = int(r['concurrency'])
    reqs   = int(r['num_prompts'])
    ttft   = float(r['TTFT'])
    t50    = float(r['TTFT_p50'])
    t99    = float(r['TTFT_p99'])
    tpot   = float(r['TPOT'])
    tp50   = float(r['TPOT_p50'])
    tp99   = float(r['TPOT_p99'])
    itl    = float(r['ITL'])
    it50   = float(r['ITL_p50'])
    it99   = float(r['ITL_p99'])
    e2e    = float(r['E2E_Latency'])
    e50    = float(r['E2E_p50'])
    e99    = float(r['E2E_p99'])
    tok_s  = float(r['token_s'])
    print(f'{in_len:>6} {conc:>6} {reqs:>5} {ttft:>7.1f} {t50:>7.1f} {t99:>7.1f}  {tpot:>7.1f} {tp50:>7.1f} {tp99:>7.1f}  {itl:>7.1f} {it50:>7.1f} {it99:>7.1f}  {e2e:>9.1f} {e50:>9.1f} {e99:>9.1f}  {tok_s:>8.1f}')

print()
print(f'CSV: $CSV_FILE')
print(f'详细: $RESULTS_DIR/result_*.jsonl')
" 2>/dev/null

echo ""
echo "================================================"
log "完成! 结果目录: $RESULTS_DIR"