---
title: PDF to Editable PPTX
emoji: 📄
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# PDF → 可编辑 PPTX（Hugging Face Space 部署说明）

这是 GitHub 仓库的 Space 版本说明文件。部署时把本文件内容**替换**成仓库根目录的 `README.md`（Spaces 要求根目录 README 带上面的 YAML 头），其余文件原样上传即可。

- SDK：Docker
- 端口：7860（`app.py` 的 uvicorn 监听端口，与 `Dockerfile` 中一致）
- 首次构建会下载 opencv / pymupdf / httpx 等依赖
- **OCR 改为调用外部 HTTP 服务**，镜像内不再包含本地模型。Space 必须能访问外部 OCR 接口，否则转换会直接报错：
  - 在 Space 的 `Settings → Variables` 里添加 `OCR_API_URL`（指向公网可达的 OCR 接口）
  - 如需鉴权再加 `OCR_API_KEY`；超时可用 `OCR_API_TIMEOUT`（默认 30 秒）
- 免费 CPU 实例可正常转换（约 5~10 秒/页，不含 OCR 接口自身耗时）
