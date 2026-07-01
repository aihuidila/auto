#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SGLang 自动化压测脚本（场景一：基准输入长度扫描）

按场景矩阵依次调用 sglang.bench_serving，固定输出长度、逐步拉长输入，
并在每个输入长度下扫描不同并发，解析每次输出的指标块，
最终汇总成 Markdown 表格（按输入长度分组）。

用法示例：
    python3 bench_sglang_auto.py --host http://localhost
    python3 bench_sglang_auto.py --host http://localhost --output-len 1024 \
        --report report.md --per-run-timeout 3600
    # 单独压低大输入场景的请求数（默认按并发缩放，可能很久）：
    python3 bench_sglang_auto.py --num-prompts 32
"""

import argparse
import csv
import math
import re
import subprocess
import sys
import time
from datetime import datetime

# ---------------------------------------------------------------------------
# 场景矩阵：输入长度(token) -> 可选并发列表
# 1K = 1024 tokens
# ---------------------------------------------------------------------------
DEFAULT_SCENARIOS = [
    (1024,   [1, 4, 8, 16, 32, 64, 128]),
    (4096,   [1, 4, 8, 16, 32, 64, 128]),
    (8192,   [1, 4, 8, 16, 32, 64, 128]),
    (16384,  [1, 4, 8, 16, 32, 64]),
    (32768,  [1, 4, 8, 16, 32]),
    (65536,  [1, 4, 8, 16]),
    (131072, [1, 4, 8]),
    (262144, [1, 4]),
]

# 输出表格的列（顺序即展示顺序）
METRIC_COLUMNS = [
    ("max_concurrency",          "Max request concurrency"),
    ("successful_requests",      "Successful requests"),
    ("request_throughput",       "Request throughput (req/s)"),
    ("output_token_throughput",  "Output token throughput (tok/s)"),
    ("total_token_throughput",   "Total token throughput (tok/s)"),
    ("e2e_mean",                 "Mean E2E Latency (ms)"),
    ("e2e_median",               "Median E2E Latency (ms)"),
    ("e2e_p99",                  "P99 E2E Latency (ms)"),
    ("ttft_mean",                "Mean TTFT (ms)"),
    ("ttft_median",              "Median TTFT (ms)"),
    ("ttft_p99",                 "P99 TTFT (ms)"),
    ("tpot_mean",                "Mean TPOT (ms)"),
    ("tpot_median",              "Median TPOT (ms)"),
    ("tpot_p99",                 "P99 TPOT (ms)"),
    ("itl_mean",                 "Mean ITL (ms)"),
    ("itl_median",               "Median ITL (ms)"),
    ("itl_p99",                  "P99 ITL (ms)"),
    ("itl_max",                  "Max ITL (ms)"),
]

# bench_serving 输出中“标签 -> 我们要存的字段名”映射
# 用正则按标签抓取数值（去掉千分位逗号）
LABEL_PATTERNS = {
    "max_concurrency":          r"Max request concurrency:\s*([\d,]+)",
    "successful_requests":      r"Successful requests:\s*([\d,]+)",
    "request_throughput":       r"Request throughput \(req/s\):\s*([\d.]+)",
    "output_token_throughput":  r"Output token throughput \(tok/s\):\s*([\d.]+)",
    "total_token_throughput":   r"Total token throughput \(tok/s\):\s*([\d.]+)",
    "e2e_mean":                 r"Mean E2E Latency \(ms\):\s*([\d.]+)",
    "e2e_median":               r"Median E2E Latency \(ms\):\s*([\d.]+)",
    "e2e_p99":                  r"P99 E2E Latency \(ms\):\s*([\d.]+)",
    "ttft_mean":                r"Mean TTFT \(ms\):\s*([\d.]+)",
    "ttft_median":              r"Median TTFT \(ms\):\s*([\d.]+)",
    "ttft_p99":                 r"P99 TTFT \(ms\):\s*([\d.]+)",
    "tpot_mean":                r"Mean TPOT \(ms\):\s*([\d.]+)",
    "tpot_median":              r"Median TPOT \(ms\):\s*([\d.]+)",
    "tpot_p99":                 r"P99 TPOT \(ms\):\s*([\d.]+)",
    "itl_mean":                 r"Mean ITL \(ms\):\s*([\d.]+)",
    "itl_median":               r"Median ITL \(ms\):\s*([\d.]+)",
    "itl_p99":                  r"P99 ITL \(ms\):\s*([\d.]+)",
    "itl_max":                  r"Max ITL \(ms\):\s*([\d.]+)",
}


import urllib.parse


def normalize_host_port(host_arg, port_arg):
    """
    bench_serving 内部会拼成 http://{host}:{port}/... ，所以 --host 不能带 scheme，
    端口要单独传。这里把各种写法归一化成 (host, port)：
      http://localhost        -> ('localhost', 30000)
      http://localhost:30000  -> ('localhost', 30000)
      localhost               -> ('localhost', 30000)
      localhost:8000          -> ('localhost', 8000)
    port_arg>0 时以 port_arg 为准。
    """
    host = host_arg.strip()
    port = port_arg if port_arg and port_arg > 0 else None

    # 去掉 scheme
    if "://" in host:
        parsed = urllib.parse.urlparse(host)
        host = parsed.hostname or host
        if port is None and parsed.port:
            port = parsed.port
    elif ":" in host:
        # localhost:8000 形式
        h, _, p = host.partition(":")
        host = h
        if port is None and p.strip().isdigit():
            port = int(p.strip())

    if port is None:
        port = 30000  # sglang 默认端口
    return host, port


def fmt_input_len(n):
    """1024 -> '1K', 4096 -> '4K'"""
    if n % 1024 == 0:
        k = n // 1024
        if k >= 1024 and k % 1024 == 0:
            return f"{k // 1024}M"
        return f"{k}K"
    return str(n)


def scale_num_prompts(concurrency, low=50, high=200, factor=8):
    """按并发缩放请求数：clamp(并发*8, 50, 200)"""
    return max(low, min(high, concurrency * factor))


def parse_bench_output(stdout):
    """从 bench_serving 的 stdout 解析指标，返回 dict。"""
    metrics = {}
    for key, pat in LABEL_PATTERNS.items():
        m = re.search(pat, stdout)
        if not m:
            metrics[key] = None
            continue
        raw = m.group(1).replace(",", "")
        # 数值统一存 float
        try:
            val = float(raw)
            if val.is_integer():
                val = int(val)
            metrics[key] = val
        except ValueError:
            metrics[key] = raw
    return metrics


def run_one(args, input_len, output_len, concurrency, num_prompts):
    """执行单次压测，返回 (metrics_dict, raw_stdout, returncode)。"""
    host, port = normalize_host_port(args.host, args.port)
    cmd = [
        sys.executable, "-m", "sglang.bench_serving",
        "--backend", args.backend,
        "--host", host,
        "--port", str(port),
        "--dataset-name", args.dataset_name,
        "--num-prompts", str(num_prompts),
        "--max-concurrency", str(concurrency),
        "--random-input-len", str(input_len),
        "--random-output-len", str(output_len),
    ]
    if args.extra_args:
        cmd += args.extra_args.split()

    label = f"input={fmt_input_len(input_len)} out={output_len} conc={concurrency} np={num_prompts}"
    print(f"\n>>> [RUN] {label}", flush=True)
    print(f"    cmd: {' '.join(cmd)}  (-> http://{host}:{port})", flush=True)

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=args.per_run_timeout,
            check=False,
        )
        out = proc.stdout or ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        print(f"    [TIMEOUT] 超过 {args.per_run_timeout}s，跳过该场景", flush=True)
        return None, out, -1
    except Exception as e:
        print(f"    [ERROR] {e}", flush=True)
        return None, str(e), -2

    elapsed = time.time() - t0
    metrics = parse_bench_output(out)
    print(f"    done in {elapsed:.1f}s (rc={rc})", flush=True)

    # 解析失败时打印尾部日志，便于排查
    if metrics.get("successful_requests") is None:
        print("    [WARN] 未能解析到 Successful requests，尾部日志：", flush=True)
        tail = "\n".join(out.splitlines()[-30:])
        print("    " + tail.replace("\n", "\n    "), flush=True)

    return metrics, out, rc


def fmt_cell(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        # 整数类指标保留整数，吞吐/延迟保留两位
        return f"{v:.2f}"
    return str(v)


def build_markdown(report_path, meta, results):
    """results: list of dict，每项含 input_len, output_len, concurrency, num_prompts, metrics..."""
    lines = []
    lines.append(f"# SGLang 自动化压测报告")
    lines.append("")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append(f"- 后端：`{meta['backend']}`")
    lines.append(f"- Host：`{meta['host']}`")
    lines.append(f"- 数据集：`{meta['dataset_name']}`")
    lines.append(f"- 固定输出长度：{meta['output_len']} tokens")
    lines.append(f"- 场景总数：{len(results)}（成功 {sum(1 for r in results if r['ok'])} / 失败 {sum(1 for r in results if not r['ok'])}）")
    lines.append("")

    # 按输入长度分组
    by_input = {}
    for r in results:
        by_input.setdefault(r["input_len"], []).append(r)

    header_cells = ["输入长度", "输出长度", "并发", "num_prompts"] + [c[1] for c in METRIC_COLUMNS]
    sep = ["---"] * len(header_cells)

    for input_len in sorted(by_input.keys()):
        group = by_input[input_len]
        lines.append(f"## 输入长度 {fmt_input_len(input_len)} ({input_len} tokens)")
        lines.append("")
        lines.append("| " + " | ".join(header_cells) + " |")
        lines.append("| " + " | ".join(sep) + " |")
        for r in group:
            row = [
                fmt_input_len(r["input_len"]),
                str(r["output_len"]),
                str(r["concurrency"]),
                str(r["num_prompts"]),
            ]
            if r["ok"]:
                for key, _ in METRIC_COLUMNS:
                    row.append(fmt_cell(r["metrics"].get(key)))
            else:
                row += ["FAILED"] * len(METRIC_COLUMNS)
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="SGLang 自动化压测（场景一：输入长度扫描）")
    ap.add_argument("--host", default="http://localhost", help="服务地址")
    ap.add_argument("--port", type=int, default=0, help="端口（0=不传，使用 host 中自带）")
    ap.add_argument("--backend", default="sglang-oai-chat")
    ap.add_argument("--dataset-name", default="random")
    ap.add_argument("--output-len", type=int, default=1024, help="固定输出长度（token）")
    ap.add_argument("--num-prompts", type=int, default=0,
                    help="固定请求数；0=按并发缩放 clamp(并发*8,50,200)")
    ap.add_argument("--per-run-timeout", type=int, default=3600,
                    help="单次压测超时（秒），大输入/高并发建议调大")
    ap.add_argument("--report", default="bench_report.md", help="Markdown 报告输出路径")
    ap.add_argument("--csv", default="", help="可选：同时输出 CSV 路径")
    ap.add_argument("--extra-args", default="", help="透传给 bench_serving 的额外参数（原样拼接）")
    ap.add_argument("--only-input", type=str, default="",
                    help="只跑指定输入长度，逗号分隔，如 1024,4096")
    ap.add_argument("--only-concurrency", type=str, default="",
                    help="只跑指定并发，逗号分隔，如 1,16")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不真正运行")
    args = ap.parse_args()

    # 过滤场景
    only_input = set()
    if args.only_input:
        only_input = {int(x) for x in args.only_input.split(",") if x.strip()}
    only_conc = set()
    if args.only_concurrency:
        only_conc = {int(x) for x in args.only_concurrency.split(",") if x.strip()}

    scenarios = []
    for input_len, conc_list in DEFAULT_SCENARIOS:
        if only_input and input_len not in only_input:
            continue
        for c in conc_list:
            if only_conc and c not in only_conc:
                continue
            scenarios.append((input_len, c))

    print(f"共 {len(scenarios)} 个场景待执行。", flush=True)

    if args.dry_run:
        for input_len, c in scenarios:
            np = args.num_prompts if args.num_prompts > 0 else scale_num_prompts(c)
            print(f"  input={fmt_input_len(input_len)} conc={c} num_prompts={np}")
        return

    results = []
    norm_host, norm_port = normalize_host_port(args.host, args.port)
    for idx, (input_len, concurrency) in enumerate(scenarios, 1):
        np = args.num_prompts if args.num_prompts > 0 else scale_num_prompts(concurrency)
        print(f"\n===== [{idx}/{len(scenarios)}] =====", flush=True)
        metrics, raw, rc = run_one(args, input_len, args.output_len, concurrency, np)
        ok = metrics is not None and metrics.get("successful_requests") is not None
        results.append({
            "input_len": input_len,
            "output_len": args.output_len,
            "concurrency": concurrency,
            "num_prompts": np,
            "ok": ok,
            "metrics": metrics or {},
            "returncode": rc,
        })
        # 每跑完一个场景就增量写一次报告，避免中途断了全丢
        meta = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backend": args.backend,
            "host": f"{norm_host}:{norm_port}",
            "dataset_name": args.dataset_name,
            "output_len": args.output_len,
        }
        build_markdown(args.report, meta, results)

    # CSV（可选）
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["input_len", "output_len", "concurrency", "num_prompts",
                        "ok"] + [c[1] for c in METRIC_COLUMNS])
            for r in results:
                row = [r["input_len"], r["output_len"], r["concurrency"],
                       r["num_prompts"], int(r["ok"])]
                if r["ok"]:
                    row += [fmt_cell(r["metrics"].get(k)) for k, _ in METRIC_COLUMNS]
                else:
                    row += [""] * len(METRIC_COLUMNS)
                w.writerow(row)

    ok_n = sum(1 for r in results if r["ok"])
    print(f"\n全部完成：成功 {ok_n}/{len(results)}", flush=True)
    print(f"报告已写入：{args.report}", flush=True)


if __name__ == "__main__":
    main()
