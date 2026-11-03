"""流水线：PDF → 版面分析 → PPTX。"""
from __future__ import annotations

import json
import os
import tempfile
import time

from .analyze import analyze_page
from .build import build_pptx
from .ocr import detect_lines
from .pdf_doc import PdfDocument


def convert_pdf(pdf_path: str, out_pptx: str, work_dir: str | None = None,
                scale: float = 2.0, pages: list[int] | None = None,
                verbose: bool = True) -> dict:
    t0 = time.time()
    doc = PdfDocument(pdf_path)
    total_pages = doc.page_count
    targets = pages if pages else list(range(total_pages))

    tmp = work_dir or tempfile.mkdtemp(prefix="pdf2pptx_")
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_pptx)) or ".", exist_ok=True)

    layouts = []
    for idx in targets:
        w_pt, h_pt = doc.page_size(idx)
        raster = doc.render(idx, scale)
        lines = detect_lines(raster.bgr, scale)
        native = doc.render(idx, 1.0)
        lay = analyze_page(raster.bgr, lines, scale, w_pt, h_pt,
                           crop_bgr=native.bgr, crop_scale=1.0)
        layouts.append(lay)
        if verbose:
            print(f"  第{idx + 1}/{total_pages}页：文本块={len(lay.blocks)} 图形={len(lay.figures)} "
                  f"表格={len(lay.tables)} 背景={lay.background['type']}")
    doc.close()

    report = build_pptx(layouts, out_pptx, tmp)
    report["source"] = os.path.basename(pdf_path)
    report["output"] = os.path.basename(out_pptx)
    report["elapsed_sec"] = round(time.time() - t0, 1)
    report["stats"] = {
        "text_boxes": sum(p["text_boxes"] for p in report["pages"]),
        "figures": sum(p["figures"] for p in report["pages"]),
        "tables": sum(p["tables"] for p in report["pages"]),
        "editable_text_chars": sum(p["editable_text_chars"] for p in report["pages"]),
    }
    return report


def convert_batch(input_dir: str, output_dir: str, verbose: bool = True) -> list[dict]:
    os.makedirs(output_dir, exist_ok=True)
    pdfs = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".pdf"))
    results = []
    for name in pdfs:
        print(f"[转换] {name}")
        out = os.path.join(output_dir, os.path.splitext(name)[0] + ".pptx")
        rep = convert_pdf(os.path.join(input_dir, name), out, verbose=verbose)
        rep["report_file"] = out + ".report.json"
        with open(rep["report_file"], "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        results.append(rep)
        print(f"  -> {out}（{rep['elapsed_sec']}s）\n")
    return results
