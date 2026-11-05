"""OCR 侧车服务：把 rapidocr-onnxruntime 包成一个 HTTP 接口。

只有在线 Demo / Docker 镜像需要它；命令行转换时也可以直接对接任意外部 OCR 服务。
接口与 converter/ocr.py 里的约定完全一致：

    POST /ocr   multipart/form-data   field: image=<PNG 二进制>
    -> {"details": [{"box": [[x0,y0],[x1,y0],[x1,y1],[x0,y1]],
                     "text": "识别文本", "score": 0.97}]}

启动：
    python ocr_server.py                      # 监听 127.0.0.1:8866
    OCR_PORT=8866 python ocr_server.py        # 自定义端口
"""
from __future__ import annotations

import io
import os

import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="RapidOCR sidecar")

# 模型加载约 1~2 秒，懒加载避免 import 时阻塞
_engine: RapidOCR | None = None


def _engine_instance() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


@app.get("/healthz")
def health() -> dict:
    return {"ok": True}


@app.post("/ocr")
def ocr(image: UploadFile = File(...)) -> dict:
    """接收 PNG，返回检测框 + 文本 + 置信度（像素坐标）。"""
    rgb = np.array(Image.open(io.BytesIO(image.file.read())).convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()
    result, _ = _engine_instance()(bgr)

    details = [
        {
            "box": [[float(x), float(y)] for x, y in box],
            "text": str(text),
            "score": float(score),
        }
        for box, text, score in (result or [])
    ]
    return {"details": details}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("OCR_HOST", "127.0.0.1"),
        port=int(os.environ.get("OCR_PORT", "8866")),
    )
