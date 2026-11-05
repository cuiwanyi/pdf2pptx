FROM python:3.11-slim

# opencv 需要 libgl；中文字体用于字号/字宽测量
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 7860

# OCR 模型随 rapidocr-onnxruntime 一起安装，进程内推理，单进程即可
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]
