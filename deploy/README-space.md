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

## 架构

- SDK：Docker，端口 7860（与 `Dockerfile` / `app.py` 一致）
- 镜像内**自带 OCR 侧车**：`start.sh` 先启动 `ocr_server.py`（RapidOCR，监听 `127.0.0.1:8866`），就绪后再启动 `uvicorn app:app`，因此 Space 不依赖任何外部 OCR 服务
- 首次构建会下载 opencv / pymupdf / httpx / rapidocr-onnxruntime 等依赖（约几分钟）
- 免费 CPU 实例可正常转换（约 5~10 秒/页）

## 步骤

1. https://huggingface.co/new-space → 名称如 `pdf2pptx-demo` → SDK 选 **Docker** → Public → Create
2. 推送代码（Space 与你刚建的 GitHub 仓库是同构的）：

   ```bash
   git remote add space https://huggingface.co/spaces/<你的HF用户名>/pdf2pptx-demo
   git push space main
   ```

   首次推送需要 HF Access Token（Settings → Access Tokens，Write 权限）作为密码。
3. Space 的 `README.md` 需要 YAML 头：把本文件前 9 行 frontmatter 保留，正文换成项目 README 内容（或用本文件直接覆盖 Space 的 README）。
4. 打开 `https://huggingface.co/spaces/<你的HF用户名>/pdf2pptx-demo`，等 Build 变绿后即可上传 PDF 测试。

## 可选：改用外部 OCR 服务

在 `Settings → Variables and secrets` 里加：

| 变量 | 说明 |
|---|---|
| `OCR_API_URL` | 外部 OCR 接口地址（覆盖默认的本地侧车） |
| `OCR_API_KEY` | 需要鉴权时填 |
| `OCR_API_TIMEOUT` | 单页超时秒数，默认 30 |

## 验收自检

- Space 出现 `Running` 后，页面上传 `samples/测试1.pdf`，应返回「文本框 / 图片 / 表格」统计并给出下载链接
- 下载的 PPTX 用 PowerPoint / WPS 打开，文字可选中编辑、图片可拖动
