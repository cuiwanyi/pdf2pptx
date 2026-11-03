"""PPTX 组装：把 PageLayout 落成真正的 PowerPoint 对象。

要点：
- 幻灯片尺寸 = PDF 页面尺寸（pt→EMU：1pt = 12700EMU），保证 1:1 还原
- 背景矩形/背景图 → 图形图片 → 文本框 → 表格，按此顺序入栈保证 z 序
- 文本框 0 内边距、关闭自动调整、关闭自动换行，并用字体实测宽度撑开框宽，避免重新排版
- 中文字体写入 a:ea，西文写入 a:latin，保证 WPS/PowerPoint 都能正常显示
"""
from __future__ import annotations

import os

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN, MSO_AUTO_SIZE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt
from lxml import etree

from .analyze import PageLayout, TableInfo, TextBlock

EMU_PER_PT = 12700
CN_FONT = "微软雅黑"

_ALIGN_MAP = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}

# ---- 字宽估算（用于撑开文本框，避免文字被压缩换行） ----
_FONT_PATH = None
for _p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if os.path.exists(_p):
        _FONT_PATH = _p
        break

_font_cache: dict[tuple[int, bool], object] = {}


def _pil_font(size_pt: float, bold: bool):
    if not _FONT_PATH:
        return None
    key = (int(round(size_pt)), bold)
    if key not in _font_cache:
        from PIL import ImageFont
        try:
            _font_cache[key] = ImageFont.truetype(_FONT_PATH, max(1, key[0]))
        except Exception:
            return None
    return _font_cache[key]


def estimate_text_width(text: str, size_pt: float, bold: bool) -> float:
    font = _pil_font(size_pt, bold)
    if font is not None:
        try:
            return float(font.getlength(text))
        except Exception:
            pass
    # 兜底估算：中文 1em，西文 0.55em
    return sum(1.0 if ord(c) > 127 else 0.55 for c in text) * size_pt


def _set_run_font(run, size_pt: float, color, bold: bool):
    font = run.font
    font.size = Pt(size_pt)
    font.bold = bold
    font.name = CN_FONT
    font.color.rgb = RGBColor(*color)
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", CN_FONT)


def _add_text(slide, blk: TextBlock, idx: int):
    # 依据实际字宽决定框宽；居中/右对齐时反向扩展，保持视觉位置不变
    max_w = max((estimate_text_width(line, blk.font_pt, blk.bold) for line in blk.text.split("\n")),
                default=0.0)
    box_w = max(blk.box[2] - blk.box[0], max_w + 2)
    left = blk.box[0]
    if blk.align == "center":
        left = (blk.box[0] + blk.box[2]) / 2 - box_w / 2
    elif blk.align == "right":
        left = blk.box[2] - box_w

    top = (blk.ink_top if blk.ink_top else blk.box[1]) - blk.font_pt * 0.20
    height = (blk.box[3] - blk.box[1]) + blk.font_pt * 0.6
    tb = slide.shapes.add_textbox(Emu(int(left * EMU_PER_PT)), Emu(int(top * EMU_PER_PT)),
                                 Emu(int(box_w * EMU_PER_PT)), Emu(int(height * EMU_PER_PT)))
    tb.name = f"txt_{idx:02d}"
    tf = tb.text_frame
    tf.word_wrap = False
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0

    lines = blk.text.split("\n")
    p = tf.paragraphs[0]
    p.alignment = _ALIGN_MAP.get(blk.align, PP_ALIGN.LEFT)
    p.line_spacing = Pt(blk.line_gap_pt)
    for i, line in enumerate(lines):
        run = p.add_run()
        run.text = line
        _set_run_font(run, blk.font_pt, blk.color, blk.bold)
        if i < len(lines) - 1:
            p.add_line_break()
    # 文本整体不参与自动缩放
    bodyPr = tf._txBody.find(qn("a:bodyPr"))
    if bodyPr is not None:
        bodyPr.set("wrap", "none")
        for child in list(bodyPr):
            if child.tag in (qn("a:normAutofit"), qn("a:spAutoFit"), qn("a:noAutofit")):
                bodyPr.remove(child)
        etree.SubElement(bodyPr, qn("a:noAutofit"))


def _cell_border(cell, color, width_pt: float = 0.75):
    tcPr = cell._tc.get_or_add_tcPr()
    for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
        ln = next((e for e in tcPr if e.tag == qn(tag)), None)
        if ln is None:
            ln = etree.Element(qn(tag))
            tcPr.insert(0, ln)
        ln.set("w", str(int(width_pt * EMU_PER_PT)))
        ln.set("cap", "flat")
        ln.set("cmpd", "sng")
        ln.set("algn", "ctr")
        for child in list(ln):
            ln.remove(child)
        fill = etree.SubElement(ln, qn("a:solidFill"))
        clr = etree.SubElement(fill, qn("a:srgbClr"))
        clr.set("val", "%02X%02X%02X" % tuple(color))


def _add_table(slide, tb_info: TableInfo, idx: int):
    gf = slide.shapes.add_table(tb_info.rows, tb_info.cols,
                               Emu(int(tb_info.box[0] * EMU_PER_PT)),
                               Emu(int(tb_info.box[1] * EMU_PER_PT)),
                               Emu(int((tb_info.box[2] - tb_info.box[0]) * EMU_PER_PT)),
                               Emu(int((tb_info.box[3] - tb_info.box[1]) * EMU_PER_PT)))
    gf.name = f"table_{idx:02d}"
    table = gf.table
    table.first_row = False
    table.horz_banding = False
    table.first_col = False
    for j, w in enumerate(tb_info.col_widths):
        table.columns[j].width = Emu(int(w * EMU_PER_PT))
    for i, hh in enumerate(tb_info.row_heights):
        table.rows[i].height = Emu(int(hh * EMU_PER_PT))

    for cell_info in tb_info.cells:
        cell = table.cell(cell_info.row, cell_info.col)
        cell.text = ""
        cell.margin_left = cell.margin_right = Emu(int(2 * EMU_PER_PT))
        cell.margin_top = cell.margin_bottom = Emu(int(1 * EMU_PER_PT))
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        if cell_info.text:
            tf = cell.text_frame
            tf.word_wrap = False
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT
            size = cell_info.font_pt or tb_info.font_pt
            n_lines = len(cell_info.text.split("\n"))
            for k, line in enumerate(cell_info.text.split("\n")):
                run = p.add_run()
                run.text = line
                _set_run_font(run, size, cell_info.color, False)
                if k < n_lines - 1:
                    p.add_line_break()
        if cell_info.fill:
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(*cell_info.fill)
        else:
            cell.fill.background()
        _cell_border(cell, tb_info.border_color)


def build_pptx(layouts: list[PageLayout], out_path: str, tmp_dir: str) -> dict:
    """把多页布局写成 pptx，返回每页的对象统计。"""
    os.makedirs(tmp_dir, exist_ok=True)
    first = layouts[0]
    prs = Presentation()
    prs.slide_width = Emu(int(first.width_pt * EMU_PER_PT))
    prs.slide_height = Emu(int(first.height_pt * EMU_PER_PT))
    blank = prs.slide_layouts[6]

    report = []
    for pno, lay in enumerate(layouts, 1):
        slide = prs.slides.add_slide(blank)
        W, H = lay.width_pt, lay.height_pt

        # 1) 背景层
        bg = lay.background
        if bg["type"] == "solid":
            shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0,
                                         Emu(int(W * EMU_PER_PT)), Emu(int(H * EMU_PER_PT)))
            shp.name = f"bg_{pno:02d}"
            shp.fill.solid()
            shp.fill.fore_color.rgb = RGBColor(*bg["color"])
            shp.line.fill.background()
            shp.shadow.inherit = False
        else:
            from PIL import Image
            p = os.path.join(tmp_dir, f"p{pno:02d}_bg.png")
            Image.fromarray(bg["image"][:, :, ::-1]).save(p)
            slide.shapes.add_picture(p, 0, 0, Emu(int(W * EMU_PER_PT)), Emu(int(H * EMU_PER_PT)))

        # 2) 图形/图片区域
        for i, fig in enumerate(lay.figures, 1):
            from PIL import Image
            fp = os.path.join(tmp_dir, f"p{pno:02d}_fig{i:02d}.png")
            Image.fromarray(fig.image[:, :, ::-1]).save(fp)
            x0, y0, x1, y1 = fig.box
            pic = slide.shapes.add_picture(fp, Emu(int(x0 * EMU_PER_PT)), Emu(int(y0 * EMU_PER_PT)),
                                           Emu(int((x1 - x0) * EMU_PER_PT)), Emu(int((y1 - y0) * EMU_PER_PT)))
            pic.name = f"img_{pno:02d}_{i:02d}"

        # 3) 文本块
        for i, blk in enumerate(lay.blocks, 1):
            _add_text(slide, blk, i)

        # 4) 表格
        for i, t in enumerate(lay.tables, 1):
            _add_table(slide, t, i)

        report.append({
            "page": pno,
            "background": bg["type"],
            "figures": len(lay.figures),
            "text_boxes": len(lay.blocks),
            "tables": len(lay.tables),
            "editable_text_chars": sum(len(b.text) for b in lay.blocks) + sum(
                len(c.text) for t in lay.tables for c in t.cells),
        })

    prs.save(out_path)
    return {"pages": report, "slide_width_pt": first.width_pt, "slide_height_pt": first.height_pt}
