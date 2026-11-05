FROM python:3.11-slim

# opencv 需要 libgl；中文字体用于字号/字宽测量
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-demo.txt ./
RUN pip install --no-cache-dir -r requirements-demo.txt

COPY . .
EXPOSE 7860

# start.sh 先拉起 OCR 侧车（127.0.0.1:8866），再启动 Demo（7860）
CMD ["sh", "start.sh"]
