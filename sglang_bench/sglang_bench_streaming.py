#!/usr/bin/env python3
"""
SGLang 模型性能压测脚本 — 通过 /v1/chat/completions 端点进行流式推理
支持多场景矩阵测试，输出完整压测报告

用法:
  # 有 ShareGPT 缓存（推荐，生成有意义文本）
  python3 sglang_bench_streaming.py http://localhost:30000 /mnt/data/models/gemma-4-12B-it ./results

  # 离线环境（随机 token ID，无需任何网络）
  python3 sglang_bench_streaming.py http://localhost:30000 /mnt/data/models/gemma-4-12B-it ./results --random-ids

依赖:
  pip install numpy
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict
from datetime import datetime

# ─────────────────────────────────────────────
# 配置区
# ─────────────────────────────────────────────
SCENARIOS = OrderedDict()

# ----- 场景 1: 基准输入长度扫描 -----
# 固定输出长度 512，逐步拉长输入，每级观察 TTFT 增长和并发扩展性
# concurrency=0 表示不限并发（burst 全部发出）
SCENARIOS["01_input_len_scan"] = {
    "desc": "基准: 输入长度扫描 — 固定 output_len=512, 逐级改变并发",
    "type": "multi_vary",
    "output_len": 512,
    "request_rate": "inf",
    # 场景级 timeout (每个单测另有各自 timeout)
    "timeout": 7200,
    "groups": [
        {"name": "L1 短基线",  "input_len": 1024,    "concurrency": [1, 4, 8, 16, 32, 64, 128],   "prompts": 100},
        {"name": "L2 短基线",  "input_len": 4096,    "concurrency": [1, 4, 8, 16, 32, 64, 128],   "prompts": 100},
        {"name": "L2 中等",    "input_len": 8192,    "concurrency": [1, 4, 8, 16, 32, 64, 128],   "prompts": 100},
        {"name": "L2 中长",    "input_len": 16384,   "concurrency": [1, 4, 8, 16, 32, 64],        "prompts": 80},
        {"name": "L2 长",      "input_len": 32768,   "concurrency": [1, 4, 8, 16, 32],            "prompts": 60},
        {"name": "L2 超长",    "input_len": 65536,   "concurrency": [1, 4, 8, 16],               "prompts": 50},
        {"name": "L2 极长",    "input_len": 131072,  "concurrency": [1, 4, 8],                   "prompts": 40},
        {"name": "L3 长",      "input_len": 262144,  "concurrency": [1, 4, 8],                   "prompts": 30},
        {"name": "L4 超长",    "input_len": 524288,  "concurrency": [1, 4],                      "prompts": 20},
        {"name": "L5 极限",    "input_len": 1048576, "concurrency": [1, 2],                      "prompts": 20},
    ],
}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def stats(arr):
    """计算 Mean / p50 / p90 / p99 / Min / Max"""
    if not arr:
        return {"mean": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}
    s = sorted(arr)
    n = len(s)
    return {
        "mean": round(sum(s) / n, 2),
        "p50":  round(s[n // 2], 2),
        "p90":  round(s[int(n * 0.90)], 2),
        "p99":  round(s[int(n * 0.99)], 2),
        "min":  round(s[0], 2),
        "max":  round(s[-1], 2),
    }


def collect_jsonl_metrics(jsonl_path):
    """从 bench_serving 输出的 JSONL 文件提取延迟指标

    JSONL 格式是单个 JSON 对象包含 summary 字段（mean/p50/p90/p99）
    和 per-request 列表字段（ttfts: [...], itls: [...]）。
    """
    try:
        with open(jsonl_path) as f:
            content = f.read().strip()
            if not content:
                return None
            data = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if not data:
        return None

    # 从 per-request 列表提取 min/max（TTFT 和 ITL 有列表，TPOT/E2E 没有）
    ttfts_raw = data.get("ttfts")
    if isinstance(ttfts_raw, list) and len(ttfts_raw) > 0:
        ttft_list = [t * 1000 for t in ttfts_raw if t is not None]
        itls_raw = data.get("itls")
        itl_list = [t * 1000 for t in itls_raw if t is not None] if isinstance(itls_raw, list) else []
    else:
        ttft_list = []
        itl_list = []

    def q(d, key, default=0):
        """Get a value, treating None as missing."""
        v = d.get(key)
        return v if v is not None else default

    # TTFT: 优先用 summary 分位数，min/max 从列表算
    ttft_s = {
        "mean": q(data, "mean_ttft_ms"),
        "p50":  q(data, "median_ttft_ms"),
        "p99":  q(data, "p99_ttft_ms"),
        "min":  min(ttft_list) if ttft_list else 0,
        "max":  max(ttft_list) if ttft_list else 0,
    }

    # TPOT: 无 per-request 列表，只从 summary 取
    tpot_s = {
        "mean": q(data, "mean_tpot_ms"),
        "p50":  q(data, "median_tpot_ms"),
        "p99":  q(data, "p99_tpot_ms"),
        "min":  q(data, "min_tpot_ms"),
        "max":  q(data, "max_tpot_ms"),
    }

    # ITL: 有 per-request 列表
    itl_s = {
        "mean": q(data, "mean_itl_ms"),
        "p50":  q(data, "median_itl_ms"),
        "p99":  q(data, "p99_itl_ms"),
        "min":  min(itl_list) if itl_list else 0,
        "max":  max(itl_list) if itl_list else 0,
    }

    # E2E: 无 per-request 列表
    e2e_s = {
        "mean": q(data, "mean_e2e_latency_ms"),
        "p50":  q(data, "median_e2e_latency_ms"),
        "p99":  q(data, "p99_e2e_latency_ms"),
        "min":  q(data, "min_e2e_latency_ms"),
        "max":  q(data, "max_e2e_latency_ms"),
    }

    # 兜底：如果 summary 全缺失，从原始列表重算
    if ttft_s["mean"] == 0 and ttft_list:
        ttft_s = stats(ttft_list)
    if itl_s["mean"] == 0 and itl_list:
        itl_s = stats(itl_list)

    num_ok = q(data, "completed", 0)
    total_input = q(data, "total_input_tokens", 0) or q(data, "total_input_text_tokens", 0)
    total_output = q(data, "total_output_tokens", 0)
    duration_s = q(data, "duration", 1) or 1

    result = {
        "num_requests": num_ok or len(ttfts_raw) if isinstance(ttfts_raw, list) else 0,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "throughput_tok_s": q(data, "total_throughput", round((total_input + total_output) / duration_s, 2)),
        "output_throughput_tok_s": q(data, "output_throughput", round(total_output / duration_s, 2)),
        "TTFT": ttft_s,
        "TPOT": tpot_s,
        "ITL":  itl_s,
        "E2E":  e2e_s,
    }

    return result


def parse_stdout_summary(stdout):
    """从 bench_serving 的 stdout 中摘取吞吐 summary"""
    result = {}
    patterns = [
        (r"Request throughput \(req/s\):\s+([\d.]+)", "req_throughput"),
        (r"Output token throughput \(tok/s\):\s+([\d.]+)", "output_tok_s"),
        (r"Total token throughput \(tok/s\):\s+([\d.]+)", "total_tok_s"),
        (r"Benchmark duration \(s\):\s+([\d.]+)", "duration_s"),
        (r"Successful requests:\s+(\d+)", "successful_requests"),
        (r"Concurrency:\s+([\d.]+)", "concurrency_avg"),
        (r"Peak concurrent requests:\s+(\d+)", "concurrency_peak"),
    ]
    for pat, key in patterns:
        m = re.search(pat, stdout)
        if m:
            result[key] = float(m.group(1)) if "." in m.group(1) else int(m.group(1))
    return result


# ─────────────────────────────────────────────
# 核心压测函数
# ─────────────────────────────────────────────

def run_benchmark(args, scenario_name, params, results_dir, use_random_ids, api_host_key):
    """
    执行一次 bench_serving 并返回解析后的指标
    """
    host, port = api_host_key
    result_file = os.path.join(results_dir, f"{scenario_name}.jsonl")

    cmd = [
        sys.executable, "-m", "sglang.bench_serving",
        "--backend", "sglang-oai-chat",
        "--host", host,
        "--port", str(port),
        "--tokenizer", params["tokenizer"],
        "--num-prompts", str(params["prompts"]),
        "--request-rate", str(params["request_rate"]),
        "--output-file", result_file,
        "--output-details",
        "--warmup-requests", "10",
        "--disable-tqdm",
        "--apply-chat-template",
        # 注意: 不加 --disable-stream，保持流式
    ]

    # concurrency=0 表示不限并发，不传 --max-concurrency
    concur = params.get("concurrency")
    if concur and concur > 0:
        cmd += ["--max-concurrency", str(concur)]

    if params.get("model"):
        cmd += ["--model", params["model"]]

    # ---- 数据集参数 ----
    if params.get("gsp_groups"):
        # GSP 场景
        cmd += [
            "--dataset-name", "generated-shared-prefix",
            "--gsp-num-groups", str(params["gsp_groups"]),
            "--gsp-prompts-per-group", str(params["gsp_per_group"]),
            "--gsp-system-prompt-len", str(params["gsp_system"]),
            "--gsp-question-len", str(params["gsp_question"]),
            "--gsp-output-len", str(params["gsp_output"]),
        ]
    else:
        # 普通场景
        dataset_name = "random-ids" if use_random_ids else "random"
        cmd += [
            "--dataset-name", dataset_name,
            "--random-input-len", str(params["input_len"]),
            "--random-output-len", str(params["output_len"]),
        ]

    # 打印命令（精简版）
    desc_key = params.get("vary_name", "")
    print(f"    └─ {desc_key}...", end=" ", flush=True)

    timeout = params.get("timeout", 600)
    start = time.time()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.time() - start
    stdout = proc.stdout
    stderr = proc.stderr

    if proc.returncode != 0:
        print(f"[FAIL exit={proc.returncode}, {elapsed:.0f}s]")
        err_lines = stderr.strip().split("\n")[-5:]
        for line in err_lines:
            if line.strip():
                print(f"      {line.strip()}")
        return None

    print(f"[OK {elapsed:.0f}s]")

    # 解析结果
    metrics = collect_jsonl_metrics(result_file)
    summary = parse_stdout_summary(stdout)

    result = {"params": params, "metrics": metrics, "summary": summary, "file": result_file}
    return result


# ─────────────────────────────────────────────
# 报告生成
# ─────────────────────────────────────────────

def format_value(v, unit=""):
    """格式化数值，避免 .00"""
    if isinstance(v, float):
        if v == int(v):
            return f"{int(v)}{unit}"
        return f"{v:.2f}{unit}"
    return f"{v}{unit}"


def print_metric_row(name, metrics_dict, unit="ms"):
    if not metrics_dict:
        return f"  {name:<10}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}  {'—':>8}\n"
    d = metrics_dict
    return (
        f"  {name:<10}"
        f"  {d['mean']:>8.1f}"
        f"  {d['p50']:>8.1f}"
        f"  {d['p90']:>8.1f}"
        f"  {d['p99']:>8.1f}"
        f"  {d['min']:>8.1f}"
        f"  {d['max']:>8.1f}\n"
    )


def generate_report(all_results, results_dir, args, model_name):
    """生成完整的压测报告 Markdown 文件"""
    report_path = os.path.join(results_dir, "bench_report.md")
    csv_path = os.path.join(results_dir, "all_results.csv")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# SGLang 模型性能压测报告\n\n")
        f.write(f"- **测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **API 端点**: {args.api_url}/v1/chat/completions (流式)\n")
        f.write(f"- **模型**: {model_name}\n")
        f.write(f"- **Tokenizer**: {args.tokenizer}\n")
        f.write(f"- **Backend**: sglang-oai-chat\n")
        f.write(f"- **数据集**: {'random-ids (离线随机ID)' if args.random_ids else 'random (ShareGPT采样文本)'}\n")
        f.write(f"- **流式**: 是\n\n")

        f.write("---\n\n## 指标说明\n\n")
        f.write("| 指标 | 含义 |\n")
        f.write("|------|------|\n")
        f.write("| **TTFT** | Time To First Token — 首 token 延迟 (ms)，反映 prefill 速度 |\n")
        f.write("| **TPOT** | Time Per Output Token — 每个输出 token 平均耗时 (ms)，反映 decode 速度 |\n")
        f.write("| **ITL** | Inter-Token Latency — token 间延迟抖动 (ms)，反映流式平滑度 |\n")
        f.write("| **E2E** | End-to-End Latency — 请求总完成时间 (ms) |\n")
        f.write("| **Tok/s per req** | 单论次吞吐 = 1000 / TPOT(p50) |\n")
        f.write("| **Output tok/s** | 系统级输出 token 吞吐量 |\n\n")

        f.write("---\n\n")

        # CSV header
        csv_rows = []
        csv_header = [
            "scenario", "vary_param", "input_len", "output_len", "concurrency",
            "request_rate", "num_prompts", "successful",
            "TTFT_mean", "TTFT_p50", "TTFT_p99",
            "TPOT_mean", "TPOT_p50", "TPOT_p99",
            "ITL_mean", "ITL_p50", "ITL_p99",
            "E2E_mean", "E2E_p50", "E2E_p99",
            "output_tok_s", "total_tok_s", "tok_per_req",
        ]
        csv_rows.append(csv_header)

        for scenario_name, runs in all_results.items():
            if not runs:
                continue
            scenario = SCENARIOS[scenario_name]
            f.write(f"## {scenario_name}: {scenario['desc']}\n\n")

            is_multi = scenario.get("type") == "multi_vary"

            # ---- multi_vary / vary 共用表格逻辑 ----
            if is_multi or scenario.get("vary"):
                if is_multi:
                    hdr_cnt = 18
                    f.write("| Input Len | Concur | Reqs"
                            " | TTFT mean(ms) | TTFT p50(ms) | TTFT p99(ms)"
                            " | TPOT mean(ms) | TPOT p50(ms) | TPOT p99(ms)"
                            " | ITL mean(ms) | ITL p50(ms) | ITL p99(ms)"
                            " | E2E mean(ms) | E2E p50(ms) | E2E p99(ms)"
                            " | Output tok/s | Total tok/s | Tok/s per req |\n")
                else:
                    hdr_cnt = 16
                    f.write(f"| {scenario['vary']} | Reqs"
                            " | TTFT mean(ms) | TTFT p50(ms) | TTFT p99(ms)"
                            " | TPOT mean(ms) | TPOT p50(ms) | TPOT p99(ms)"
                            " | ITL mean(ms) | ITL p50(ms) | ITL p99(ms)"
                            " | E2E mean(ms) | E2E p50(ms) | E2E p99(ms)"
                            " | Output tok/s | Total tok/s | Tok/s per req |\n")
                f.write("|" + "---|" * hdr_cnt + "\n")

                for run in runs:
                    if run is None:
                        continue
                    p = run["params"]
                    m = run["metrics"]
                    s = run["summary"]
                    if m is None:
                        continue

                    reqs = m["num_requests"]
                    t = m["TTFT"]
                    tp = m["TPOT"]
                    it = m["ITL"]
                    e2 = m["E2E"]
                    out_tok_s = s.get("output_tok_s", m["output_throughput_tok_s"]) if s else m["output_throughput_tok_s"]
                    tot_tok_s = s.get("total_tok_s", m["throughput_tok_s"]) if s else m["throughput_tok_s"]
                    tok_per_req = round(1000 / tp["p50"], 1) if tp["p50"] > 0 else 0

                    metric_cols = (
                        f" | {t['mean']:.0f} | {t['p50']:.0f} | {t['p99']:.0f}"
                        f" | {tp['mean']:.1f} | {tp['p50']:.1f} | {tp['p99']:.1f}"
                        f" | {it['mean']:.1f} | {it['p50']:.1f} | {it['p99']:.1f}"
                        f" | {e2['mean']:.0f} | {e2['p50']:.0f} | {e2['p99']:.0f}"
                        f" | {out_tok_s:.1f} | {tot_tok_s:.1f} | {tok_per_req}"
                    )

                    if is_multi:
                        ilen = p.get("input_len", "")
                        conc = p.get("concurrency", "")
                        conc_label = "∞" if conc == 0 else str(conc)
                        f.write(f"| {ilen} / {conc_label} | {reqs}{metric_cols} |\n")
                        csv_rows.append([
                            scenario_name, f"{ilen}/{conc}",
                            str(ilen), str(p.get("output_len", "")),
                            str(conc), str(p.get("request_rate", "")),
                            str(p.get("prompts", "")), str(reqs),
                            str(t['mean']), str(t['p50']), str(t['p90']), str(t['p99']),
                            str(tp['mean']), str(tp['p50']), str(tp['p90']), str(tp['p99']),
                            str(it['mean']), str(it['p50']), str(it['p90']), str(it['p99']),
                            str(e2['mean']), str(e2['p50']), str(e2['p90']), str(e2['p99']),
                            str(out_tok_s), str(tot_tok_s), str(tok_per_req),
                        ])
                    else:
                        vary_val = p.get("vary_name", "")
                        f.write(f"| {vary_val} | {reqs}{metric_cols} |\n")
                        csv_rows.append([
                            scenario_name, str(vary_val),
                            str(p.get("input_len", "")), str(p.get("output_len", "")),
                            str(p.get("concurrency", "")), str(p.get("request_rate", "")),
                            str(p.get("prompts", "")), str(reqs),
                            str(t['mean']), str(t['p50']), str(t['p90']), str(t['p99']),
                            str(tp['mean']), str(tp['p50']), str(tp['p90']), str(tp['p99']),
                            str(it['mean']), str(it['p50']), str(it['p90']), str(it['p99']),
                            str(e2['mean']), str(e2['p50']), str(e2['p90']), str(e2['p99']),
                            str(out_tok_s), str(tot_tok_s), str(tok_per_req),
                        ])

            else:
                # 单次运行场景
                run = runs[0] if runs else None
                if run and run["metrics"]:
                    m = run["metrics"]
                    s = run["summary"]
                    f.write("### 结果\n\n")
                    f.write("| 指标 | Mean | p50 | p90 | p99 | Min | Max |\n")
                    f.write("|------|------|-----|-----|-----|-----|------|\n")
                    f.write(f"| TTFT (ms) | {m['TTFT']['mean']:.1f} | {m['TTFT']['p50']:.1f} | {m['TTFT']['p90']:.1f} | {m['TTFT']['p99']:.1f} | {m['TTFT']['min']:.1f} | {m['TTFT']['max']:.1f} |\n")
                    f.write(f"| TPOT (ms) | {m['TPOT']['mean']:.1f} | {m['TPOT']['p50']:.1f} | {m['TPOT']['p90']:.1f} | {m['TPOT']['p99']:.1f} | {m['TPOT']['min']:.1f} | {m['TPOT']['max']:.1f} |\n")
                    f.write(f"| ITL (ms)  | {m['ITL']['mean']:.1f} | {m['ITL']['p50']:.1f} | {m['ITL']['p90']:.1f} | {m['ITL']['p99']:.1f} | {m['ITL']['min']:.1f} | {m['ITL']['max']:.1f} |\n")
                    f.write(f"| E2E (ms)  | {m['E2E']['mean']:.1f} | {m['E2E']['p50']:.1f} | {m['E2E']['p90']:.1f} | {m['E2E']['p99']:.1f} | {m['E2E']['min']:.1f} | {m['E2E']['max']:.1f} |\n\n")
                    out_s = s.get("output_tok_s", m["output_throughput_tok_s"]) if s else m["output_throughput_tok_s"]
                    tot_s = s.get("total_tok_s", m["throughput_tok_s"]) if s else m["throughput_tok_s"]
                    f.write(f"- Requests: {m['num_requests']}\n")
                    f.write(f"- Output throughput: **{out_s:.1f} tok/s**\n")
                    f.write(f"- Total throughput: **{tot_s:.1f} tok/s**\n\n")

                    tok_pr = round(1000 / m["TPOT"]["p50"], 1) if m["TPOT"]["p50"] > 0 else 0
                    csv_rows.append([
                        scenario_name, "single",
                        str(run["params"].get("input_len", "")), str(run["params"].get("output_len", "")),
                        str(run["params"].get("concurrency", "")), "inf",
                        str(run["params"].get("prompts", "")), str(m["num_requests"]),
                        str(m['TTFT']['mean']), str(m['TTFT']['p50']), str(m['TTFT']['p90']), str(m['TTFT']['p99']),
                        str(m['TPOT']['mean']), str(m['TPOT']['p50']), str(m['TPOT']['p90']), str(m['TPOT']['p99']),
                        str(m['ITL']['mean']), str(m['ITL']['p50']), str(m['ITL']['p90']), str(m['ITL']['p99']),
                        str(m['E2E']['mean']), str(m['E2E']['p50']), str(m['E2E']['p90']), str(m['E2E']['p99']),
                        str(out_s), str(tot_s), str(tok_pr),
                    ])

            f.write("\n---\n\n")

        # ---- 瓶颈分析 ----
        f.write("## 瓶颈分析\n\n")
        f.write("### Prefill 瓶颈 (TTFT / input_len)\n\n")
        f.write("TTFT 除以输入长度得到每个输入 token 的 prefill 时间 (ms/tok)，值越低 prefill 效率越高。\n\n")

        # 从场景1抓 low-concurrency 数据（取 concur=1 的数据行）
        for sn, runs in all_results.items():
            if sn.startswith("01") and runs:
                f.write("| Input Len | Concur | TTFT mean(ms) | ms per input token |\n")
                f.write("|-----------|--------|---------------|--------------------|\n")
                for run in runs:
                    if run and run["metrics"] and run["params"].get("concurrency") == 1:
                        il = run["params"]["input_len"]
                        ttft = run["metrics"]["TTFT"]["mean"]
                        per_tok = ttft / il if il > 0 else 0
                        f.write(f"| {il} | 1 | {ttft:.0f} | {per_tok:.4f} |\n")
                f.write("\n")
                break

        f.write("### Decode 瓶颈 (TPOT)\n\n")
        f.write("TPOT 越低 decode 越快，单流吞吐 = 1000/TPOT(ms) tok/s。\n\n")

        # 从场景1抓 concur 变化对 TPOT 的影响（固定一个输入长度，如 1024）
        for sn, runs in all_results.items():
            if sn.startswith("01") and runs:
                # 选输入长度 = 1024 的数据
                f.write("| Concur | TPOT p50(ms) | Tok/s per request | Output tok/s (系统) |\n")
                f.write("|--------|--------------|--------------------|----------------------|\n")
                for run in runs:
                    if run and run["metrics"] and run["params"].get("input_len") == 1024:
                        conc = run["params"]["concurrency"]
                        tpot = run["metrics"]["TPOT"]["p50"]
                        single = round(1000 / tpot, 1) if tpot > 0 else 0
                        sys_tok = run["metrics"]["output_throughput_tok_s"]
                        conc_label = "∞" if conc == 0 else str(conc)
                        f.write(f"| {conc_label} | {tpot:.1f} | {single} | {sys_tok:.1f} |\n")
                f.write("\n")
                break

        # ---- 结论 ----
        f.write("## 结论与建议\n\n")
        f.write("1. **最大吞吐**: ")
        max_tok_s = 0
        max_scenario = ""
        for scenario_name, runs in all_results.items():
            for run in runs:
                if run and run.get("summary"):
                    ts = run["summary"].get("total_tok_s", 0)
                    if ts > max_tok_s:
                        max_tok_s = ts
                        max_scenario = scenario_name
        f.write(f"系统总吞吐峰值 **{max_tok_s:.1f} tok/s**（场景: {max_scenario}）\n")
        f.write("2. 建议生产环境并发控制在 TTFT 开始显著恶化的拐点以下\n")
        f.write("3. 具体调优方向请结合上述分场景数据\n\n")

        f.write("---\n")
        f.write(f"报告自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  原始数据: `result_*.jsonl`\n")

    # 写 CSV
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        for row in csv_rows:
            writer.writerow(row)

    print(f"\n  📄 报告: {report_path}")
    print(f"  📊 数据: {csv_path}")
    return report_path


# ─────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SGLang 模型性能压测 — /v1/chat/completions 流式推理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 有 ShareGPT 缓存
  python3 sglang_bench_streaming.py http://localhost:30000 /mnt/data/models/gemma-4-12B-it ./results

  # 离线（纯随机 ID）
  python3 sglang_bench_streaming.py http://localhost:30000 /mnt/data/models/gemma-4-12B-it ./results --random-ids

  # 只跑指定场景（逗号分隔）
  python3 sglang_bench_streaming.py http://localhost:30000 /mnt/data/models/gemma-4-12B-it ./results --only 01,03
        """,
    )
    parser.add_argument("api_url", help="SGLang 服务地址，如 http://localhost:30000")
    parser.add_argument("tokenizer", help="Tokenizer 路径或模型名")
    parser.add_argument("output_dir", nargs="?", default="./bench_results", help="输出目录 (默认: ./bench_results)")
    parser.add_argument("--random-ids", action="store_true", help="使用 random-ids 数据集（离线兼容，无需网络）")
    parser.add_argument("--only", help="只跑指定场景编号，逗号分隔 如 01,03")
    parser.add_argument("--no-model-name", action="store_true", help="不传 --model 参数（服务端自动识别）")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不实际执行")
    args = parser.parse_args()

    # 解析 API URL
    api_url = args.api_url.rstrip("/")
    api_url_clean = api_url.replace("http://", "").replace("https://", "")
    api_url_clean = api_url_clean.replace("/v1", "").replace("/chat/completions", "")
    if ":" in api_url_clean:
        host, port = api_url_clean.split(":")
        port = int(port)
    else:
        host = api_url_clean
        port = 80

    api_host_key = (host, port)

    # 检查服务可用
    model_name = "unknown"
    if args.dry_run:
        model_name = "<model-from-server>"
        print("  [dry-run] 跳过服务检查")
    else:
        print("🔍 检查服务可用性...", end=" ", flush=True)
        ret = os.system(f"curl -sf {api_url}/health > /dev/null 2>&1")
        if ret != 0:
            print(f"✗ 端点不可用: {api_url}/health")
            ret = os.system(f"curl -sf {api_url}/v1/models > /dev/null 2>&1")
            if ret != 0:
                print(f"✗ 无法连接: {api_url}")
                sys.exit(1)
        print("✓")

        try:
            r = subprocess.run(
                ["curl", "-sf", f"{api_url}/v1/models"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                model_name = data.get("data", [{}])[0].get("id", "unknown")
        except Exception:
            pass
    print(f"  🧠 模型: {model_name}\n")

    # 创建输出目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(args.output_dir, timestamp)
    os.makedirs(results_dir, exist_ok=True)

    # 筛选场景
    if args.only:
        selected = set(args.only.split(","))
        scenarios_to_run = OrderedDict(
            (k, v) for k, v in SCENARIOS.items()
            if any(k.startswith(s) for s in selected)
        )
    else:
        scenarios_to_run = SCENARIOS

    print(f"📋 测试计划 ({len(scenarios_to_run)} 个场景):")
    for name, sc in scenarios_to_run.items():
        if sc.get("type") == "multi_vary":
            total_tests = sum(len(g["concurrency"]) for g in sc["groups"])
            print(f"  [{name}] {sc['desc']}")
            print(f"         {total_tests} 个单测,  {len(sc['groups'])} 组输入长度")
            for g in sc["groups"]:
                tag = f" [{g['name']}]" if g.get("name") else ""
                conc_str = ",".join(str(c) if c != 0 else "∞" for c in g["concurrency"])
                print(f"           {g['input_len']:>7} tok{tag}  concur=[{conc_str}]  prompts={g.get('prompts', sc.get('prompts', 50))}")
        else:
            print(f"  [{name}] {sc['desc']} (单次)")
    print()

    if args.dry_run:
        print("🏁 Dry-run 模式，不执行。")
        return

    # 执行测试
    all_results = OrderedDict()
    total_runs = 0
    for sc in scenarios_to_run.values():
        if sc.get("type") == "multi_vary":
            total_runs += sum(len(g["concurrency"]) for g in sc["groups"])
        else:
            total_runs += 1

    run_count = 0

    for scenario_name, scenario in scenarios_to_run.items():
        print(f"\n{'='*60}")
        print(f" 场景: {scenario_name}")
        print(f" {scenario['desc']}")
        print(f"{'='*60}")

        runs = []
        scenario_timeout = scenario.get("timeout")

        if scenario.get("type") == "multi_vary":
            # ---- multi_vary: 遍历 groups → 每个 group 遍历 concurrency ----
            for gi, group in enumerate(scenario["groups"]):
                ilen = group["input_len"]
                olen = scenario["output_len"]
                group_prompts = group.get("prompts", scenario.get("prompts", 50))
                tag = group.get("name", "")

                for ci, conc in enumerate(group["concurrency"]):
                    run_count += 1
                    conc_label = "∞" if conc == 0 else str(conc)
                    desc = f"{tag}/{ilen}tok/concur={conc_label}" if tag else f"{ilen}tok/concur={conc_label}"
                    print(f"  [{run_count}/{total_runs}] {desc}...", end=" ", flush=True)

                    params = {
                        "input_len": ilen,
                        "output_len": olen,
                        "concurrency": conc,
                        "request_rate": scenario["request_rate"],
                        "prompts": group_prompts,
                        "vary_name": f"{ilen}_{conc}",
                        "tokenizer": args.tokenizer,
                    }
                    if not args.no_model_name:
                        params["model"] = model_name
                    if scenario_timeout:
                        params["timeout"] = scenario_timeout

                    sfx = f"{scenario_name}_in{ilen}_c{conc}"
                    result = run_benchmark(
                        args, sfx, params,
                        results_dir, args.random_ids, api_host_key,
                    )
                    runs.append(result)
                    time.sleep(0.5)
        else:
            run_count += 1
            print(f"  [{run_count}/{total_runs}] ", end="")
            params = dict(scenario["fixed"])
            params["tokenizer"] = args.tokenizer
            if not args.no_model_name:
                params["model"] = model_name
            params["vary_name"] = "single"
            if scenario_timeout:
                params["timeout"] = scenario_timeout

            result = run_benchmark(
                args, scenario_name, params,
                results_dir, args.random_ids, api_host_key,
            )
            runs.append(result)
            time.sleep(0.5)

        all_results[scenario_name] = runs

        # 打印该场景摘要
        for run in runs:
            if run and run["metrics"] and run["summary"]:
                s = run["summary"]
                tok = s.get("output_tok_s", "—")
                print(f"      → req={s.get('successful_requests','—')}  out_tok/s={tok}")

    # 生成报告
    print(f"\n{'='*60}")
    print(f" 生成报告...")
    print(f"{'='*60}")
    report = generate_report(all_results, results_dir, args, model_name)

    # 打印报告路径和摘要
    csv_path = os.path.join(results_dir, "all_results.csv")
    print(f"\n✅ 测试完成！结果目录: {results_dir}")
    print(f"   📄 报告: {report}")
    print(f"   📊 CSV:  {csv_path}")
    print(f"   📁 JSONL: {results_dir}/*.jsonl")


if __name__ == "__main__":
    main()