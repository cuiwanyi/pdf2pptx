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
- 首次构建会下载 onnxruntime / opencv 等依赖，之后的启动即可直接使用
- 免费 CPU 实例可正常转换（约 5~10 秒/页）
