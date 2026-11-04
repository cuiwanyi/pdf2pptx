"""OCR：文本行检测 + 识别（走外部 HTTP API）。

对外接口保持不变：detect_lines(bgr, scale, min_score) -> list[OcrLine]，
上游 pipeline.py / analyze.py 无感知。

API 配置（全部走环境变量，默认值直接对接 RapidOCR 官方 HTTP 服务）：
    OCR_API_URL        接口地址，默认 http://127.0.0.1:8866/ocr
    OCR_API_TIMEOUT    单次请求超时秒数，默认 30
    OCR_API_KEY        可选鉴权，加到 Authorization: Bearer <key>

默认假设的 API 契约（兼容 RapidOCR 官方 HTTP、PaddleOCR-Hub、多数开源 OCR 网关）：
    POST <OCR_API_URL>
    Content-Type: multipart/form-data
    field: image=<PNG 二进制>
    响应 JSON，二选一：
      a) {"details": [{"box": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
                        "text": "...", "score": 0.97}]}   ← RapidOCR / PaddleOCR-Hub
      b) [{"box": [...], "text": "...", "score": 0.97}]    ← 直接 list 也支持

坐标约定：API 返回像素坐标（px），内部按原逻辑 / scale 换算成 pt，
所以 scale 参数语义不变。
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

import cv2
import httpx
import numpy as np
from PIL import Image


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


# --- API 配置 ---
API_URL = os.environ.get("OCR_API_URL", "http://127.0.0.1:8866/ocr")
API_TIMEOUT = float(os.environ.get("OCR_API_TIMEOUT", "30"))
API_KEY = os.environ.get("OCR_API_KEY", "")


def _encode_image(bgr: np.ndarray) -> bytes:
    """BGR numpy -> PNG 二进制，走 Pillow 转 RGB 再写内存，避免 imencode 中文路径问题。"""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue()


def _call_api(img_bytes: bytes) -> list[tuple[list[list[float]], str, float]]:
    """调外部 OCR HTTP 服务，返回 [(box4点, text, score), ...]。失败抛 RuntimeError。"""
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}

    try:
        resp = httpx.post(
            API_URL,
            files={"image": ("page.png", img_bytes, "image/png")},
            headers=headers,
            timeout=API_TIMEOUT,
        )
    except httpx.RequestError as e:
        raise RuntimeError(f"OCR API 网络错误 ({API_URL}): {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(
            f"OCR API HTTP {resp.status_code} ({API_URL}): {resp.text[:300]}"
        )

    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"OCR API 响应不是合法 JSON ({API_URL}): {resp.text[:300]}") from e

    # 支持两种常见格式：{"details":[...]} 或 直接 list
    items = data.get("details", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError(f"OCR API 响应格式异常 ({API_URL}): 期望 list / {{details:list}}")

    out = []
    for it in items:
        box = it.get("box", it.get("bounding_box"))
        text = str(it.get("text", it.get("content", "")))
        score = float(it.get("score", it.get("confidence", 0.0)))
        if not box or len(box) != 4:
            continue
        out.append((box, text, score))
    return out


def detect_lines(bgr: np.ndarray, scale: float, min_score: float = 0.45) -> list[OcrLine]:
    res = _call_api(_encode_image(bgr))

    lines: list[OcrLine] = []
    for box, text, score in res:
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
