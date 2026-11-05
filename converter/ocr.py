"""OCR：文本行检测 + 识别（进程内 RapidOCR / onnxruntime，纯 CPU）。

对外接口：detect_lines(bgr, scale, min_score) -> list[OcrLine]，
上游 pipeline.py / analyze.py 只依赖这一个函数。

模型：PP-OCRv4 det + rec + 方向分类，随 rapidocr-onnxruntime 一起安装，
无需联网、无需 GPU、无需外部服务。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass
class OcrLine:
    text: str
    x0: float  # pt
    y0: float
    x1: float
    y1: float
    score: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


# 引擎懒加载（首次调用初始化 ~1-2s），线程安全
_engine = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR

                _engine = RapidOCR()
    return _engine


def detect_lines(bgr: np.ndarray, scale: float, min_score: float = 0.45) -> list[OcrLine]:
    result, _ = _get_engine()(bgr)

    lines: list[OcrLine] = []
    for item in result or []:
        box, text, score = item[0], str(item[1]), float(item[2])
        if score < min_score or not text.strip() or len(box) != 4:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        lines.append(
            OcrLine(
                text=text.strip(),
                x0=min(xs) / scale,
                y0=min(ys) / scale,
                x1=max(xs) / scale,
                y1=max(ys) / scale,
                score=score,
            )
        )
    return lines
