"""命令行入口。

用法：
    python cli.py <input.pdf|输入目录> [-o 输出.pptx|输出目录] [-p 1,3,5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from converter.pipeline import convert_batch, convert_pdf


def main():
    ap = argparse.ArgumentParser(description="PDF → 可编辑 PPTX")
    ap.add_argument("input", help="PDF 文件或包含 PDF 的目录")
    ap.add_argument("-o", "--output", help="输出 pptx 路径或输出目录")
    ap.add_argument("-p", "--pages", help="只转换指定页，逗号分隔，如 1,3,5")
    ap.add_argument("-s", "--scale", type=float, default=2.0, help="分析用渲染倍率，默认 2.0")
    args = ap.parse_args()

    if os.path.isdir(args.input):
        out_dir = args.output or os.path.join(os.path.dirname(args.input.rstrip("\\/")), "output")
        results = convert_batch(args.input, out_dir)
        print(json.dumps([{k: r[k] for k in ("source", "output", "elapsed_sec", "stats")} for r in results],
                         ensure_ascii=False, indent=2))
        return

    pages = None
    if args.pages:
        pages = [int(x) - 1 for x in args.pages.split(",") if x.strip()]
    out = args.output or os.path.splitext(args.input)[0] + ".pptx"
    rep = convert_pdf(args.input, out, scale=args.scale, pages=pages)
    report_path = out + ".report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f"完成：{out}\n报告：{report_path}\n统计：{rep['stats']}")


if __name__ == "__main__":
    sys.exit(main())
