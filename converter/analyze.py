"""版面分析：把「一页栅格图 + OCR 行」还原成结构化元素。

产出四类对象：
1) 背景层：整页纯色 → 原生矩形；复杂底纹/渐变/整页大图 → 作为背景图片（去字后）
2) 图形/图片区域：从像素连通域中切出的视觉块，逐个作为独立图片对象
3) 文本块：把 OCR 行按对齐/行距/字号聚成段落，估计字号、颜色、粗细、对齐
4) 表格：检测网格线 → OCR 文本入格 → 输出为 PPT 原生表格
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .ocr import OcrLine, detect_lines

# ---------- 数据结构 ----------


@dataclass
class TextBlock:
    text: str                      # 含换行的整段文字
    box: tuple[float, float, float, float]   # pt
    font_pt: float
    color: tuple[int, int, int]    # RGB
    bold: bool
    align: str                     # left / center / right
    line_gap_pt: float
    ink_top: float = 0.0           # 首行墨迹顶边 y（用于精确定位文本框）


@dataclass
class Figure:
    box: tuple[float, float, float, float]   # pt
    image: np.ndarray              # BGR 裁剪图


@dataclass
class TableCell:
    row: int
    col: int
    box: tuple[float, float, float, float]
    text: str
    color: tuple[int, int, int]
    fill: tuple[int, int, int] | None
    font_pt: float = 0.0


@dataclass
class TableInfo:
    box: tuple[float, float, float, float]
    rows: int                      # 数据行数（不含框线）
    cols: int
    cells: list[TableCell]
    col_widths: list[float]        # pt
    row_heights: list[float]
    font_pt: float
    color: tuple[int, int, int]
    border_color: tuple[int, int, int]


@dataclass
class PageLayout:
    width_pt: float
    height_pt: float
    background: dict               # {"type":"solid","color":RGB} 或 {"type":"image","image":ndarray}
    figures: list[Figure] = field(default_factory=list)
    blocks: list[TextBlock] = field(default_factory=list)
    tables: list[TableInfo] = field(default_factory=list)


# ---------- 小工具 ----------

_CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3000, 0x303F),
    (0xFF00, 0xFFEF), (0x2E80, 0x2EFF),
)


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in _CJK_RANGES)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if _is_cjk(c)) / len(text)


def _pt_box(box_px, scale):
    return (box_px[0] / scale, box_px[1] / scale, box_px[2] / scale, box_px[3] / scale)


def _px_box(box_pt, scale):
    return (int(round(box_pt[0] * scale)), int(round(box_pt[1] * scale)),
            int(round(box_pt[2] * scale)), int(round(box_pt[3] * scale)))


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _iou(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / (_area(a) + _area(b) - inter + 1e-6)


def _contains(outer, inner, margin: float = 0.0) -> bool:
    return (outer[0] - margin <= inner[0] and outer[1] - margin <= inner[1]
            and outer[2] + margin >= inner[2] and outer[3] + margin >= inner[3])


# ---------- 背景与内容掩码 ----------

def _dominant_color(bgr: np.ndarray, mask: np.ndarray | None = None) -> tuple[int, int, int]:
    """量化后取众数颜色，返回 BGR。"""
    step = 16
    if mask is not None:
        pix = bgr[mask]
        if pix.size == 0:
            pix = bgr.reshape(-1, 3)
    else:
        pix = bgr.reshape(-1, 3)
    q = (pix // step).astype(np.int64)
    key = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, counts = np.unique(key, return_counts=True)
    best = vals[int(np.argmax(counts))]
    sel = pix[key == best]
    return tuple(int(v) for v in np.median(sel, axis=0))


def _content_mask(bgr: np.ndarray, bg: tuple[int, int, int]) -> np.ndarray:
    """与背景色差异大 或 有明显边缘 → 视为视觉内容。"""
    diff = np.max(np.abs(bgr.astype(np.int16) - np.array(bg, dtype=np.int16)), axis=2)
    mask = (diff > 20).astype(np.uint8) * 255
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 30, 100)
    mask[edges > 0] = 255
    return mask


def _text_mask(shape, lines: list[OcrLine], scale: float, pad: int = 5) -> np.ndarray:
    m = np.zeros(shape[:2], np.uint8)
    for ln in lines:
        x0, y0, x1, y1 = _px_box(ln.box, scale)
        cv2.rectangle(m, (max(0, x0 - pad), max(0, y0 - pad)),
                      (min(shape[1] - 1, x1 + pad), min(shape[0] - 1, y1 + pad)), 255, -1)
    return m


def _text_color(bgr: np.ndarray, box_pt, scale: float) -> tuple[int, int, int]:
    """取文字笔画颜色：以包围盒四边中位色为底，再对差异大的像素取中位。"""
    x0, y0, x1, y1 = _px_box(box_pt, scale)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bgr.shape[1], x1), min(bgr.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0)
    patch = bgr[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
    border = np.concatenate([patch[:3].ravel(), patch[-3:].ravel()])
    base = np.median(border.reshape(-1, 3), axis=0) if border.size else np.array([255, 255, 255])
    dist = np.max(np.abs(patch - base), axis=1)
    ink = patch[dist > 60]
    if ink.size == 0:
        ink = patch[dist > 25]
    if ink.size == 0:
        return (0, 0, 0)
    b, g, r = np.median(ink, axis=0)
    return (int(r), int(g), int(b))


# ---------- 文本块分组 ----------

def _size_from_line(ln: OcrLine) -> float:
    """粗略字号（仅用于段落合并等辅助判断，精确值走墨迹拟合）。"""
    ratio = _cjk_ratio(ln.text)
    factor = 1.10 * ratio + 1.45 * (1 - ratio)
    return ln.h * factor


# ---- 字号拟合：用页面真实墨迹尺寸 + 目标字体度量反推 ----
_FONT_PATH = None
for _p in (r"C:\Windows\Fonts\msyh.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    import os as _os
    if _os.path.exists(_p):
        _FONT_PATH = _p
        break

_fit_cache: dict = {}


def _pil_font(size: float):
    if not _FONT_PATH or size <= 0:
        return None
    key = max(4, int(round(size)))
    if key not in _fit_cache:
        from PIL import ImageFont
        try:
            _fit_cache[key] = ImageFont.truetype(_FONT_PATH, key)
        except Exception:
            return None
    return _fit_cache[key]


def _pil_metrics(text: str, size: float) -> tuple[float, float] | None:
    font = _pil_font(size)
    if font is None:
        return None
    try:
        b = font.getbbox(text)
        return float(b[2] - b[0]), float(b[3] - b[1])
    except Exception:
        return None


def _text_width(text: str, size: float) -> float:
    font = _pil_font(size)
    if font is None:
        return sum(1.0 if ord(c) > 127 else 0.55 for c in text) * size
    try:
        return float(font.getlength(text))
    except Exception:
        return sum(1.0 if ord(c) > 127 else 0.55 for c in text) * size


def _ink_bbox(bgr: np.ndarray, box_pt, scale: float) -> tuple[float, float] | None:
    """量出包围盒内真实墨迹的宽高（pt），比 OCR 框更贴近字形。"""
    x0, y0, x1, y1 = _px_box(box_pt, scale)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bgr.shape[1], x1), min(bgr.shape[0], y1)
    if x1 <= x0 + 2 or y1 <= y0 + 2:
        return None
    patch = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    base = float(np.median(np.concatenate([gray[:2].ravel(), gray[-2:].ravel()])))
    ink = (np.abs(gray.astype(np.float32) - base) > 55)
    if ink.sum() < 5:
        return None
    rows = np.where(ink.sum(axis=1) >= max(1, int(0.02 * ink.shape[1])))[0]
    cols = np.where(ink.sum(axis=0) >= max(1, int(0.02 * ink.shape[0])))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return (float(cols[-1] - cols[0] + 1) / scale, float(rows[-1] - rows[0] + 1) / scale,
            (y0 + float(rows[0])) / scale, (y0 + float(rows[-1])) / scale,
            (x0 + float(cols[0])) / scale)


def _ink_segments(bgr: np.ndarray, box_pt, scale: float):
    """返回行内墨迹的横向分段 [(x0,x1)...]（px）与墨迹高度。"""
    x0, y0, x1, y1 = _px_box(box_pt, scale)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bgr.shape[1], x1), min(bgr.shape[0], y1)
    if x1 <= x0 + 2 or y1 <= y0 + 2:
        return [], 0
    gray = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    base = float(np.median(np.concatenate([gray[:2].ravel(), gray[-2:].ravel()])))
    ink = np.abs(gray.astype(np.float32) - base) > 55
    colsum = ink.sum(axis=0)
    active = colsum >= max(1, int(0.03 * ink.shape[0]))
    segs, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            segs.append((start, i))
            start = None
    if start is not None:
        segs.append((start, len(active)))
    rows = np.where(ink.sum(axis=1) >= max(1, int(0.02 * ink.shape[1])))[0]
    h_px = int(rows[-1] - rows[0] + 1) if rows.size else ink.shape[0]
    return segs, h_px


def _restore_spaces(bgr: np.ndarray, ln: OcrLine, scale: float, size: float) -> str:
    """OCR 常把词间空格吞掉：用墨迹间隙 + 字宽比例把空格补回来。"""
    text = ln.text
    if " " in text or len(text) < 3:
        return text
    segs, h_px = _ink_segments(bgr, ln.box, scale)
    if len(segs) < 2:
        return text
    gaps = [segs[i + 1][0] - segs[i][1] for i in range(len(segs) - 1)]
    med = float(np.median(gaps))
    cjk = _cjk_ratio(text)
    # 中文词间距通常 ≥0.3em，西文词间距约 0.3~0.5em，但字间空隙更小
    thr = max(1.6 * med, (0.30 if cjk > 0.5 else 0.15) * h_px)
    big_idx = [i for i, g in enumerate(gaps) if g > thr]
    if not big_idx:
        return text

    # 纯西文且分段≈字符数时按序号映射；中文用字宽比例映射更稳
    if cjk < 0.3 and 0.6 <= len(segs) / len(text) <= 1.6:
        idxs = sorted(set(big_idx))
    else:
        font = _pil_font(size)
        if font is None:
            return text
        char_w = [max(1.0, font.getlength(c)) for c in text]
        cum = np.cumsum(char_w) / sum(char_w)
        ink_span = max(1.0, segs[-1][1] - segs[0][0])
        centers = [(segs[i][1] + segs[i + 1][0]) / 2.0 for i in big_idx]
        idxs = sorted({int(np.argmin(np.abs(cum - (c - segs[0][0]) / ink_span))) for c in centers})

    out = []
    for i, ch in enumerate(text):
        out.append(ch)
        if i in idxs and i < len(text) - 1:
            out.append(" ")
    return "".join(out)


def _fit_font_size(bgr: np.ndarray, ln: OcrLine, scale: float) -> tuple[float, float, str]:
    """返回 (字号pt, 墨迹顶边y(pt), 补全空格后的文本)。"""
    ink = _ink_bbox(bgr, ln.box, scale)
    rough = _size_from_line(ln)
    if ink is None:
        return rough, ln.y0, ln.text
    ink_w, ink_h, ink_y0 = ink[0], ink[1], ink[2]
    text = _restore_spaces(bgr, ln, scale, rough)
    # 1) 以墨迹高度定字号（中文墨迹≈0.86em，西文大写≈0.72em）
    size = ink_h / (0.86 if _cjk_ratio(text) > 0.5 else 0.72)
    # 2) 宽度偏差过大时（OCR 多字/漏字、字距特殊）用宽度校正一次
    met = _pil_metrics(text, size)
    if met and ink_w > 0:
        w, _ = met
        dev = w / ink_w
        if abs(dev - 1.0) > 0.08:
            size = float(np.clip(size / dev, size * 0.75, size * 1.25))
    return float(max(5.0, min(size, 400.0))), ink_y0, text


def _stroke_ratio(bgr: np.ndarray, box_pt, scale: float) -> float:
    """笔画粗细 / 字号，用于判断是否加粗。"""
    x0, y0, x1, y1 = _px_box(box_pt, scale)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(bgr.shape[1], x1), min(bgr.shape[0], y1)
    patch = bgr[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    base = float(np.median(np.concatenate([gray[:2].ravel(), gray[-2:].ravel()])))
    ink = (np.abs(gray.astype(np.float32) - base) > 60).astype(np.uint8)
    if ink.sum() < 20:
        return 0.0
    dt = cv2.distanceTransform(ink, cv2.DIST_L2, 3)
    vals = dt[ink > 0]
    return float(2.0 * np.median(vals) / max(1.0, ink.shape[0]))


def _group_blocks(lines: list[OcrLine]) -> list[list[OcrLine]]:
    blocks: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda l: (l.y0, l.x0)):
        best, best_gap = None, 1e9
        for blk in blocks:
            last = blk[-1]
            h = (last.h + ln.h) / 2
            gap = ln.y0 - last.y1
            if gap < -0.6 * h or gap > 0.8 * h:
                continue
            if max(last.h, ln.h) / min(last.h, ln.h + 1e-6) > 1.6:
                continue
            # 水平关系：重叠足够 / 左对齐 / 居中对齐
            overlap = min(last.x1, ln.x1) - max(last.x0, ln.x0)
            aligned_left = abs(last.x0 - ln.x0) < 0.9 * h
            aligned_center = abs(last.cx - ln.cx) < 0.6 * max(last.w, ln.w) * 0.5
            if overlap < 0.3 * min(last.w, ln.w) and not aligned_left and not aligned_center:
                continue
            if gap < best_gap:
                best, best_gap = blk, gap
        if best is None:
            blocks.append([ln])
        else:
            best.append(ln)
    return blocks


def _make_block(lines: list[OcrLine], bgr: np.ndarray, scale: float,
                body_size: float) -> TextBlock:
    x0 = min(l.x0 for l in lines)
    y0 = min(l.y0 for l in lines)
    x1 = max(l.x1 for l in lines)
    y1 = max(l.y1 for l in lines)

    fits = [_fit_font_size(bgr, l, scale) for l in lines]
    font_pt = float(np.median([f[0] for f in fits]))
    ink_top = min(f[1] for f in fits)
    texts = [f[2] for f in fits]
    gap = float(np.median([b.y0 - a.y0 for a, b in zip(lines, lines[1:])])) if len(lines) > 1 else font_pt * 1.25
    gap = max(gap, font_pt * 0.95)

    # 颜色：按字数加权的中位数
    cols = [_text_color(bgr, l.box, scale) for l in lines]
    weights = [max(1, len(l.text)) for l in lines]
    color = tuple(int(np.average([c[i] for c in cols], weights=weights)) for i in range(3))

    # 对齐：比较各行左/右/中心
    if len(lines) >= 2:
        left_spread = np.std([l.x0 for l in lines])
        center_spread = np.std([l.cx for l in lines])
        right_spread = np.std([l.x1 for l in lines])
        if left_spread <= center_spread and left_spread <= right_spread:
            align = "left"
        elif center_spread <= right_spread:
            align = "center"
        else:
            align = "right"
    else:
        align = "left"

    # 加粗：笔画粗细 / 字号 的比例（中文常规≈0.08，加粗≈0.15+）
    stroke = float(np.median([_stroke_ratio(bgr, l.box, scale) for l in lines]))
    bold = stroke > 0.115 or font_pt >= max(body_size * 1.45, 16.0)

    text = "\n".join(texts)
    return TextBlock(text=text, box=(x0, y0, x1, y1), font_pt=round(font_pt, 1),
                     color=color, bold=bold, align=align, line_gap_pt=round(gap, 1),
                     ink_top=round(ink_top, 1))


# ---------- 表格检测 ----------

def _line_coords(binary: np.ndarray, axis: int, ratio: float, max_thick: int = 8) -> list[int]:
    """提取线坐标；只保留"细"的分组，过滤掉整块填色区域（色块会被误当成长线）。"""
    proj = (binary > 0).sum(axis=axis) / binary.shape[1 - axis]
    idx = np.where(proj >= ratio)[0]
    if idx.size == 0:
        return []
    groups, cur = [], [idx[0]]
    for v in idx[1:]:
        if v - cur[-1] <= 2:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    return [int(np.mean(g)) for g in groups if len(g) <= max_thick]


def _line_segments(mask: np.ndarray, orient: str, min_len: int, max_thick: int = 10) -> list[tuple]:
    """返回线条段的 bbox（按轮廓），orient='h' 取横线，'v' 取竖线。"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    segs = []
    for c in cnts:
        x, y, cw, ch = cv2.boundingRect(c)
        if orient == "h" and cw >= min_len and ch <= max_thick:
            segs.append((y + ch / 2.0, x, x + cw))
        elif orient == "v" and ch >= min_len and cw <= max_thick:
            segs.append((x + cw / 2.0, y, y + ch))
    return segs


def _merge_positions(vals: list[float], tol: float = 5.0) -> list[float]:
    if not vals:
        return []
    vals = sorted(vals)
    out = [vals[0]]
    for v in vals[1:]:
        if v - out[-1] > tol:
            out.append(v)
        else:
            out[-1] = (out[-1] + v) / 2.0
    return out


def _text_column_bounds(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int,
                        row_ys: list[float]) -> list[float]:
    """竖向无文字的空隙即列分隔：用文字墨迹的列投影找带，再取带间中点。"""
    ink = (gray[y0:y1, x0:x1] < 170).astype(np.uint8)
    for ry in row_ys:                       # 去掉分隔线附近的行，只看正文
        a, b = int(ry - y0 - 6), int(ry - y0 + 6)
        if a < ink.shape[0] and b > 0:
            ink[max(0, a):min(ink.shape[0], b), :] = 0
    occ = ink.sum(axis=0) > 0
    bands, start = [], None
    for i, v in enumerate(occ):
        if v and start is None:
            start = i
        elif not v and start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(occ)))
    # 合并距离很近的带（同一单元格内的词间距）
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < 25:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    if len(merged) < 2:
        return []
    bounds = [float(x0)] + [float(x0 + (merged[i][1] + merged[i + 1][0]) / 2.0)
                            for i in range(len(merged) - 1)] + [float(x1)]
    return bounds


def _ocr_cell(bgr: np.ndarray, box_pt, scale: float, factor: float = 2.0) -> str:
    """单元格单独再 OCR：解决跨列被整体识别成一行的情况。"""
    x0, y0, x1, y1 = _px_box(box_pt, scale)
    pad = 2
    crop = bgr[max(0, y0 + pad):max(0, y1 - pad), max(0, x0 + pad):max(0, x1 - pad)]
    if crop.size == 0:
        return ""
    big = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    ls = detect_lines(big, scale * factor)
    return "\n".join(l.text for l in sorted(ls, key=lambda l: (l.y0, l.x0)))


def _detect_table(bgr: np.ndarray, lines: list[OcrLine], scale: float) -> TableInfo | None:
    """表格还原：横线定行距，竖线（无竖线时用文字列投影）定列边界。"""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    soft = ((gray < 244).astype(np.uint8)) * 255      # 浅色表格线也能进来
    # 先扣掉整块填色（表头蓝底、色块），只留细线，否则色块会被当成一条粗线
    blob = cv2.morphologyEx(soft, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    soft = cv2.bitwise_and(soft, cv2.bitwise_not(blob))

    hlines = cv2.morphologyEx(soft, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, int(w * 0.05)), 1)))
    vlines = cv2.morphologyEx(soft, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, int(h * 0.03)))))

    hsegs = _line_segments(hlines, "h", min_len=int(0.15 * w))
    if len(hsegs) < 3:
        return None

    # 按 x 覆盖范围把横线分组，取成员最多的那组（页眉/页脚装饰条会自成一组）
    groups: list[list[tuple]] = []
    for s in sorted(hsegs, key=lambda s: (s[1], s[0])):
        for g in groups:
            rep = g[0]                          # 与组内代表线比较，避免越并越宽
            inter = min(rep[2], s[2]) - max(rep[1], s[1])
            if inter > 0.92 * min(rep[2] - rep[1], s[2] - s[1]):
                g.append(s)
                break
        else:
            groups.append([s])
    grp = max(groups, key=len)
    if len(grp) < 3:
        return None
    gx0 = int(min(s[1] for s in grp))
    gx1 = int(max(s[2] for s in grp))
    ys = _merge_positions([s[0] for s in grp])
    if len(ys) < 3 or (ys[-1] - ys[0]) < 0.1 * h or (gx1 - gx0) < 0.25 * w:
        return None

    gy0, gy1 = int(ys[0]), int(ys[-1])
    row_h = float(np.median(np.diff(ys))) if len(ys) > 1 else 40.0

    # 行的横线常常只画在数值列上：用区域内的文字左右边界把表格横向补全
    row_ocr = [ln for ln in lines if gy0 / scale - 2 <= ln.cy <= gy1 / scale + 2]
    if row_ocr:
        gx0 = min(gx0, int(min(ln.x0 for ln in row_ocr) * scale) - 6)
        gx1 = max(gx1, int(max(ln.x1 for ln in row_ocr) * scale) + 6)

    # 列边界优先用「文字列投影」：美化后的表格多是白线分列，竖线检测不可靠
    xs = _text_column_bounds(gray, gx0, gy0, gx1, gy1, ys)
    if len(xs) < 3:
        vsegs = [s for s in _line_segments(vlines, "v", min_len=int(0.5 * (gy1 - gy0)))
                 if gx0 - 10 <= s[0] <= gx1 + 10]
        xs = _merge_positions([s[0] for s in vsegs]) if len(vsegs) >= 3 else []
    if len(xs) < 3:
        return None
    xs[0], xs[-1] = float(gx0), float(gx1)

    # 表头的上下框线常被色带盖住：用「上一行有文字且覆盖多列」来判断并补出表头行
    header_rows = 0
    for k in (1, 2):
        top, bot = gy0 - k * row_h, gy0 - (k - 1) * row_h
        cols_hit = set()
        for ln in lines:
            if top <= ln.cy * scale <= bot:
                j = next((j for j in range(len(xs) - 1) if xs[j] <= ln.cx * scale < xs[j + 1]), None)
                if j is not None:
                    cols_hit.add(j)
        if len(cols_hit) >= max(2, int(0.6 * (len(xs) - 1))):
            header_rows = k
        else:
            break
    if header_rows:
        ys = [gy0 - header_rows * row_h + i * row_h for i in range(header_rows)] + ys
        gy0 = int(ys[0])

    rows, cols = len(ys) - 1, len(xs) - 1
    box_pt = _pt_box((gx0, gy0, gx1, gy1), scale)

    # 把 OCR 行按中心点分配到单元格；同时标记「跨列的行」，这些格子要单独重识别
    cell_lines: dict[tuple[int, int], list[OcrLine]] = {}
    need_ocr: set[tuple[int, int]] = set()
    for ln in lines:
        cy = ln.cy * scale
        if not (ys[0] <= cy <= ys[-1]):
            continue
        ci = next((i for i in range(rows) if ys[i] <= cy < ys[i + 1]), None)
        if ci is None:
            continue
        lx0, lx1 = ln.x0 * scale, ln.x1 * scale
        js = [j for j in range(cols) if min(lx1, xs[j + 1]) - max(lx0, xs[j]) > 4]
        if not js:
            continue
        if len(js) > 1:
            for j in js:
                need_ocr.add((ci, j))
            cj = js[0]
        else:
            cj = js[0]
        cell_lines.setdefault((ci, cj), []).append(ln)

    # 结构校验：单元格文字填充率足够高，才认定是表格（图表网格线会被这条规则挡掉）
    if rows < 3 or cols < 2:
        return None
    fill_ratio = len(cell_lines) / float(rows * cols)
    row_filled = sum(1 for i in range(rows)
                     if sum(1 for j in range(cols) if (i, j) in cell_lines) >= max(2, cols // 2))
    if fill_ratio < 0.5 or row_filled < 0.7 * rows or (gy1 - gy0) > 0.62 * h:
        return None

    cells: list[TableCell] = []
    text_colors = []
    cell_font = []
    for i in range(rows):
        for j in range(cols):
            cbox = _pt_box((xs[j], ys[i], xs[j + 1], ys[i + 1]), scale)
            ls = sorted(cell_lines.get((i, j), []), key=lambda l: (l.y0, l.x0))
            text = "\n".join(l.text for l in ls)
            # 跨列的行：格子单独重识别，避免把相邻列的文字并进一格
            if (i, j) in need_ocr or (ls and any(l.x0 < cbox[0] - 1.5 or l.x1 > cbox[2] + 1.5 for l in ls)):
                text = _ocr_cell(bgr, cbox, scale) or text
            color = _text_color(bgr, cbox, scale) if text else (0, 0, 0)
            cell_size = 0.0
            if text:
                text_colors.append(color)
                probe = ls[0] if ls else OcrLine(text=text, x0=cbox[0], y0=cbox[1],
                                                 x1=cbox[2], y1=cbox[3], score=1.0)
                cell_size = float(_fit_font_size(bgr, probe, scale)[0])
                # 字号按列宽收一下，避免单元格内换行
                avail = (cbox[2] - cbox[0]) - 4
                need = max((_text_width(t, cell_size) for t in text.split("\n")), default=0.0)
                if need > avail > 0:
                    cell_size = max(4.0, cell_size * avail / need)
                cell_font.append(cell_size)
            # 单元格底色：主体色占比足够高才填色
            px0, py0, px1, py1 = _px_box(cbox, scale)
            pad = 3
            patch = bgr[py0 + pad:py1 - pad, px0 + pad:px1 - pad]
            fill = None
            if patch.size:
                bgc = _dominant_color(patch)
                far = np.max(np.abs(patch.reshape(-1, 3).astype(np.int16) -
                                    np.array(bgc, dtype=np.int16)), axis=1)
                if float((far < 30).mean()) > 0.55 and np.min(bgc) < 250:
                    fill = (int(bgc[2]), int(bgc[1]), int(bgc[0]))
            cells.append(TableCell(row=i, col=j, box=cbox, text=text, color=color,
                                   fill=fill, font_pt=round(cell_size, 1)))

    color = tuple(int(np.median([c[k] for c in text_colors])) for k in range(3)) if text_colors else (0, 0, 0)
    font_pt = round(float(np.median(cell_font)), 1) if cell_font else 10.0
    border = _text_color(bgr, box_pt, scale)
    return TableInfo(
        box=box_pt, rows=rows, cols=cols, cells=cells,
        col_widths=[(xs[j + 1] - xs[j]) / scale for j in range(cols)],
        row_heights=[(ys[i + 1] - ys[i]) / scale for i in range(rows)],
        font_pt=font_pt, color=color, border_color=border,
    )


# ---------- 主流程 ----------

def analyze_page(bgr: np.ndarray, lines: list[OcrLine], scale: float,
                 width_pt: float, height_pt: float,
                 crop_bgr: np.ndarray | None = None, crop_scale: float = 1.0) -> PageLayout:
    """bgr/scale 用于分析（高分辨率更利于 OCR 与连通域），
    crop_bgr/crop_scale 用于出图（默认取原生分辨率，避免二次上采样导致糊）。"""
    crop_src = bgr if crop_bgr is None else crop_bgr
    cscale = scale if crop_bgr is None else crop_scale

    h, w = bgr.shape[:2]
    total = float(w * h)

    bg_bgr = _dominant_color(bgr)

    # 1) 表格
    table = _detect_table(bgr, lines, scale)
    table_lines: list[OcrLine] = []
    if table:
        for ln in lines:
            if _contains(table.box, ln.box, 1.0):
                table_lines.append(ln)
    text_lines = [l for l in lines if l not in table_lines]

    # 2) 内容掩码 → 图形区域
    exclude = _text_mask(bgr.shape, text_lines, scale, pad=6)
    if table:
        x0, y0, x1, y1 = _px_box(table.box, scale)
        cv2.rectangle(exclude, (x0, y0), (x1, y1), 255, -1)
    mask = _content_mask(bgr, bg_bgr)
    mask[exclude > 0] = 0

    k_close = max(9, int(w * 0.012)) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cand = []
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        if area < 0.0004 * total and not (cw >= 0.2 * w and ch <= 0.03 * h):
            continue
        density = area / max(1.0, cw * ch)
        if density < 0.03:
            # 极稀疏的整页细线/边框，交给背景层，避免它把别的块串成一个大框
            continue
        cand.append([x, y, x + cw, y + ch, area, density])
    # 仅合并「真正贴合」的块，避免整页元素被串成一个大块
    merged = True
    while merged:
        merged = False
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i][:4], cand[j][:4]
                gap = max(0, max(a[0] - b[2], b[0] - a[2]), max(a[1] - b[3], b[1] - a[3]))
                if _iou(a, b) > 0.6 or gap < 0.004 * w:
                    cand[i][:4] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    cand[i][4] += cand[j][4]
                    cand.pop(j)
                    merged = True
                    break
            if merged:
                break

    fig_boxes_px = [tuple(c[:4]) for c in cand]
    dens = {tuple(c[:4]): c[4] / max(1.0, (c[2] - c[0]) * (c[3] - c[1])) for c in cand}
    # 整页视觉主图：包围盒过半 + 像素足够多 + 密度稀疏 → 说明是「整页底图/装饰」
    big_boxes = [b for b in fig_boxes_px
                 if _area(b) > 0.5 * total and dens.get(b, 1.0) * _area(b) > 0.15 * total]
    full_bleed = bool(big_boxes) and min(dens.get(b, 1.0) for b in big_boxes) < 0.45

    # 3) 选出图形块，并吸收块内小字（图表坐标轴等，避免碎成一堆散文本框）
    body_h = float(np.median([l.h for l in text_lines])) if text_lines else 18.0
    absorbed: list[OcrLine] = []
    fig_items: list[tuple[tuple[float, float, float, float], tuple[int, int, int, int]]] = []
    if not full_bleed:
        for fb in sorted(fig_boxes_px, key=lambda b: -_area(b)):   # 大块在前，保证 z 序
            bpt = tuple(_pt_box(fb, scale))
            thin_line = (fb[2] - fb[0]) >= 0.12 * w and (fb[3] - fb[1]) <= 0.012 * h
            if _area(fb) < 0.0004 * total and not thin_line:
                # 过小的碎片：留给背景层，避免对象碎片化
                continue
            for ln in text_lines:
                if ln not in absorbed and _contains(bpt, ln.box, 1.5) \
                        and ln.h <= 0.85 * body_h and _area(fb) < 0.35 * total:
                    absorbed.append(ln)
            fig_items.append((bpt, fb))

    # 4) 生成「去字底图」：被吸收的小字（图表内标签）保留，其余文字全部擦除
    remaining_lines = [l for l in text_lines if l not in absorbed]
    em = _text_mask(crop_src.shape, remaining_lines + table_lines, cscale, pad=3)
    if table:
        x0, y0, x1, y1 = _px_box(table.box, cscale)
        cv2.rectangle(em, (x0, y0), (x1, y1), 255, -1)
    clean = cv2.inpaint(crop_src, em, 4, cv2.INPAINT_TELEA) if em.any() else crop_src.copy()

    # 5) 文本块
    blocks = []
    if remaining_lines:
        body_size = float(np.median([_size_from_line(l) for l in remaining_lines]))
        for grp in _group_blocks(remaining_lines):
            blocks.append(_make_block(grp, bgr, scale, body_size))

    # 6) 图形对象：一律从「去字底图」裁剪，避免图片里再叠一份原文字
    figures: list[Figure] = []
    for bpt, fb in fig_items:
        cx0, cy0, cx1, cy1 = _px_box(bpt, cscale)
        figures.append(Figure(box=bpt, image=clean[cy0:cy1, cx0:cx1].copy()))

    # 7) 背景板：把图形区域填成底色，剩下的就是背景层
    plate = clean.copy()
    if fig_items:
        outside = np.ones(clean.shape[:2], np.uint8) * 255
        for bpt, _ in fig_items:
            x0, y0, x1, y1 = _px_box(bpt, cscale)
            cv2.rectangle(outside, (x0, y0), (x1, y1), 0, -1)
        fill = _dominant_color(clean, mask=outside > 0) if (outside > 0).any() else _dominant_color(clean)
        for bpt, _ in fig_items:
            x0, y0, x1, y1 = _px_box(bpt, cscale)
            plate[y0:y1, x0:x1] = fill

    if full_bleed:
        background = {"type": "image", "image": plate, "note": "整页大图：作为背景层（文字已擦除）"}
    else:
        qt = (plate.reshape(-1, 3) // 24).astype(np.int64)
        key = qt[:, 0] * 4096 + qt[:, 1] * 64 + qt[:, 2]
        vals, counts = np.unique(key, return_counts=True)
        share = float(counts.max() / key.size)
        solid_bgr = _dominant_color(plate)
        # 还要确认「没有残留的小装饰」：残留明显时保留图片底，避免丢元素
        residual = _content_mask(plate, solid_bgr)
        n2, _, st2, _ = cv2.connectedComponentsWithStats(residual, 8)
        leftover = sum(1 for i in range(1, n2) if st2[i][4] > 0.0004 * total)
        if share > 0.85 and leftover <= 2:
            background = {"type": "solid", "color": (int(solid_bgr[2]), int(solid_bgr[1]), int(solid_bgr[0]))}
        else:
            background = {"type": "image", "image": plate, "note": "渐变/底纹背景层"}

    return PageLayout(width_pt=width_pt, height_pt=height_pt, background=background,
                      figures=figures, blocks=blocks, tables=[table] if table else [])
