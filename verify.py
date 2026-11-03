"""验证脚本：把生成的 PPTX 渲染成图，与原 PDF 逐页做像素相似度对比，
同时统计每页的可编辑对象数量（文本框 / 图片 / 表格 / 形状）。

Windows 下用 WPS 演示（KWPP COM）导出 PDF；Linux 下回退到 LibreOffice。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
import pymupdf


def pptx_to_pdf(pptx_path: str, pdf_path: str) -> bool:
    pptx_abs = os.path.abspath(pptx_path)
    pdf_abs = os.path.abspath(pdf_path)
    if sys.platform.startswith("win"):
        ps = (
            "$app = New-Object -ComObject KWPP.Application;"
            f"$pres = $app.Presentations.Open('{pptx_abs}', $true, $false, $false);"
            f"$pres.SaveAs('{pdf_abs}', 32);$pres.Close();$app.Quit()"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True)
        if os.path.exists(pdf_abs):
            return True
        print("WPS 导出失败:", r.stderr[:300])
        return False
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                    "--outdir", os.path.dirname(pdf_abs), pptx_abs],
                   capture_output=True)
    return os.path.exists(pdf_abs)


def _render_pdf(path: str, dpi: int = 110) -> list[np.ndarray]:
    doc = pymupdf.open(path)
    out = []
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=dpi)
        arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 3:
            out.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        else:
            out.append(arr[:, :, :3].copy())
    doc.close()
    return out


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    c1, c2 = 0.01 * 255 ** 2, 0.03 * 255 ** 2
    mu1, mu2 = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
    s1 = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu1 * mu1
    s2 = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu2 * mu2
    s12 = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu1 * mu2
    return float(((2 * mu1 * mu2 + c1) * (2 * s12 + c2) /
                  ((mu1 ** 2 + mu2 ** 2 + c1) * (s1 + s2 + c2))).mean())


def inventory(pptx_path: str) -> list[dict]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(pptx_path)
    rows = []
    for i, slide in enumerate(prs.slides, 1):
        item = {"page": i, "textbox": 0, "picture": 0, "table": 0, "shape": 0, "chars": 0}
        for sh in slide.shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                item["picture"] += 1
            elif sh.has_table:
                item["table"] += 1
                for cell in [c for row in sh.table.rows for c in row.cells]:
                    item["chars"] += len(cell.text)
            elif sh.has_text_frame:
                item["textbox"] += 1
                item["chars"] += len(sh.text_frame.text)
            else:
                item["shape"] += 1
        rows.append(item)
    return rows


def _imwrite(path: str, img: np.ndarray) -> None:
    """cv2.imwrite 不支持中文路径，这里用 imencode + 二进制写入；对比图存 jpg 控制体积。"""
    ext = os.path.splitext(path)[1] or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, 85] if ext == ".jpg" else []
    ok, buf = cv2.imencode(ext, img, params)
    if ok:
        with open(path, "wb") as f:
            f.write(buf.tobytes())


def compare(source_pdf: str, pptx_path: str, out_dir: str, dpi: int = 110,
            pages: list[int] | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    tmp_pdf = os.path.join(tempfile.mkdtemp(prefix="verify_"), "render.pdf")
    if not pptx_to_pdf(pptx_path, tmp_pdf):
        raise RuntimeError("PPTX 渲染失败（需要 WPS 或 LibreOffice）")
    src_all = _render_pdf(source_pdf, dpi)
    if pages:
        src = [src_all[i] for i in pages]
    else:
        src = src_all
    gen = _render_pdf(tmp_pdf, dpi)

    rows = []
    for i in range(min(len(src), len(gen))):
        s = _ssim(src[i], gen[i])
        h = max(src[i].shape[0], gen[i].shape[0])
        gen_r = cv2.resize(gen[i], (src[i].shape[1], src[i].shape[0]))
        side = np.hstack([src[i], np.full((h, 12, 3), 255, np.uint8), gen_r])
        _imwrite(os.path.join(out_dir, f"compare_{i + 1:02d}.jpg"), side)
        rows.append({"slide": i + 1, "source_page": (pages[i] + 1) if pages else i + 1,
                     "ssim": round(s, 4)})
    mean = float(np.mean([p["ssim"] for p in rows])) if rows else 0.0
    result = {"source": os.path.basename(source_pdf), "pptx": os.path.basename(pptx_path),
              "mean_ssim": round(mean, 4), "pages": rows,
              "inventory": inventory(pptx_path)}
    with open(os.path.join(out_dir, "verify.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python verify.py <源PDF> <生成PPTX> [输出目录] [页码,如1,4,8]")
        sys.exit(1)
    pg = [int(x) - 1 for x in sys.argv[4].split(",")] if len(sys.argv) > 4 else None
    res = compare(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "verify_out", pages=pg)
    print(json.dumps({"mean_ssim": res["mean_ssim"],
                      "pages": res["pages"]}, ensure_ascii=False, indent=2))
