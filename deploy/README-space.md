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
- OCR 在进程内运行（RapidOCR + onnxruntime），镜像自带模型，单进程即可，不依赖任何外部服务
- 首次构建会下载 opencv / pymupdf / rapidocr-onnxruntime 等依赖（约几分钟）
- 免费 CPU 实例可正常转换（约 5~10 秒/页）

> 注意：Hugging Face 目前对 Docker / Gradio Space 收取计算资源费用，免费账号的配额为 0（Build 后会直接 PAUSED 并提示 `Quota exceeded`），部署前需确认账号已升级 PRO。

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

当前是本地 OCR。若改为外部服务，需要先在 `converter/ocr.py` 里恢复 HTTP 调用分支，再在 `Settings → Variables and secrets` 里配置：

| 变量 | 说明 |
|---|---|
| `OCR_API_URL` | 外部 OCR 接口地址 |
| `OCR_API_KEY` | 需要鉴权时填 |
| `OCR_API_TIMEOUT` | 单页超时秒数，默认 30 |

## 验收自检

- Space 出现 `Running` 后，页面上传 `samples/测试1.pdf`，应返回「文本框 / 图片 / 表格」统计并给出下载链接
- 下载的 PPTX 用 PowerPoint / WPS 打开，文字可选中编辑、图片可拖动
