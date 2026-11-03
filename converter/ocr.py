"""OCR：文本行检测 + 识别。

采用 RapidOCR（PP-OCR det/rec 模型，onnxruntime 推理），
输出按行给出轴对齐包围盒（已换算回 pt 坐标）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from rapidocr_onnxruntime import RapidOCR


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


_engine = None
_lock = threading.Lock()


def _get_engine() -> RapidOCR:
    """RapidOCR 初始化较重（加载 onnx 模型），进程内复用一份。"""
    global _engine
    with _lock:
        if _engine is None:
            _engine = RapidOCR()
        return _engine


def detect_lines(bgr: np.ndarray, scale: float, min_score: float = 0.45) -> list[OcrLine]:
    res, _ = _get_engine()(bgr)
    lines: list[OcrLine] = []
    for box, text, score in (res or []):
        if score < min_score or not text.strip():
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
                score=float(score),
            )
        )
    return lines
