"""PDF 读取：逐页栅格化成 BGR 图像。

坐标约定：全项目统一使用 PDF 点（pt）作为逻辑坐标，
像素坐标 = pt * scale。默认 scale=2（即 144dpi），
因为原始素材页图只有 72dpi，2 倍上采样能明显提升 OCR 精度。
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf


@dataclass
class PageRaster:
    """一页的图像与其元信息。"""

    index: int
    bgr: np.ndarray        # 栅格图（BGR，2 倍分辨率）
    scale: float           # 像素 / pt
    width_pt: float        # 页面宽（pt）
    height_pt: float       # 页面高（pt）

    @property
    def px_w(self) -> int:
        return self.bgr.shape[1]

    @property
    def px_h(self) -> int:
        return self.bgr.shape[0]


class PdfDocument:
    def __init__(self, path: str):
        self.path = path
        self._doc = pymupdf.open(path)
        if self._doc.needs_pass:
            raise ValueError("PDF 已加密，无法读取")

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page_size(self, index: int) -> tuple[float, float]:
        rect = self._doc[index].rect
        return rect.width, rect.height

    def render(self, index: int, scale: float = 2.0) -> PageRaster:
        page = self._doc[index]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        samples = np.frombuffer(pix.samples, dtype=np.uint8)
        arr = samples.reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            arr = arr[:, :, :3]
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_GRAY2BGR)
        w_pt, h_pt = page.rect.width, page.rect.height
        return PageRaster(index=index, bgr=bgr, scale=scale, width_pt=w_pt, height_pt=h_pt)

    def close(self):
        self._doc.close()
