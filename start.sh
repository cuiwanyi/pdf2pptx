#!/bin/sh
# 容器入口：先起 OCR 侧车（127.0.0.1:8866），就绪后再起 Demo Web 服务（7860）
set -e

python ocr_server.py &

i=0
until python -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1',8866))==0 else 1)"; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo "OCR sidecar failed to start in 60s" >&2
        exit 1
    fi
    sleep 1
done

exec uvicorn app:app --host 0.0.0.0 --port 7860
